import json
import random
import re
import time
import requests
from pathlib import Path
from typing import Optional

from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format
from diplomacy.engine.message import Message


# --------------------------------------------------------------------------- #
#  Configuration                                                              #
# --------------------------------------------------------------------------- #
MODEL_BY_POWER = {
    "AUSTRIA": "mistral:7b-instruct",
    "ENGLAND": "llama3:8b",
    "FRANCE": "qwen2.5:7b-instruct",
    "GERMANY": "deepseek-r1:14b",
    "ITALY": "gemma3:12b",
    "RUSSIA": "phi4",
    "TURKEY": "starling-lm:7b-alpha",
} 
OUTPUT_FILE  = Path("game_state:ID1.json")
DIALOGUE_FILE = Path("dialogue_logID1.json")
MAX_YEAR = 1904
DIALOGUE_LOG: list[dict] = []
PHASE_MESSAGES = {}

# optimize by reducing tokens
CHAT_TOKENS = 96
ORDER_TOKENS = 64

# Rule‑book appended to every prompt (≈ 140 tokens)
RULEBOOK = """
### DIPLOMACY – COMPLETE ORDER‑FORMAT REFERENCE (7‑Player Standard Map) ###

GENERAL
• Seven powers (AUS, ENG, FRA, GER, ITA, RUS, TUR) each control Armies (A) and/or Fleets (F).
• Each unit occupies ONE province (land or sea).  Only one unit may occupy a province at a time.
• All orders are written in **three‑letter province abbreviations** (e.g., PAR, TYR, NTH).
• All players write orders simultaneously; adjudication follows standard Diplomacy rules.
• Players may form alliances, propose peace deals, offer support, or deceive and betray one another. Strategic diplomacy is as important as military tactics.
• There is no obligation to honor agreements. Trust must be earned — or exploited.

─────────────────────────
ORDER TYPES & SYNTAX
─────────────────────────
1. **Hold**  – Unit stays in place  
   `A PAR H`   (Army in Paris holds)

2. **Move** – Unit attempts to enter adjacent destination province  
   `A PAR - BUR` (Army Paris → Burgundy)

3. **Support**  – Unit adds strength +1 to another unit's Hold or Move  
   `A MAR S A PAR - BUR`   (Army Marseilles supports Paris→Burgundy)  
   Rules:  
   • Supporter must be able to move to the target's **DEST** (for support‑to‑move)  
   • Support is **cut** if the supporter is attacked from any province other than the one
     receiving support.

4. **Convoy**  – Fleet transports an Army across water  
   `F ENG C A LON - BEL`   (Fleet English Channel convoys Army London→Belgium)  
   • Only Fleets in **sea provinces** may convoy.  
   • Write a separate Convoy order **for every Fleet** in the chain.  
   • If any convoying Fleet is dislodged, the convoy fails.

5. **Build / Disband / Retreat** – Not required in MOVE phases and therefore **omitted here**.
   This prompt concerns only Spring & Fall Movement orders.

─────────────────────────
COMBAT & DISLODGEMENT
─────────────────────────
• A unit may MOVE into an **occupied** province.  Compare strengths:
    – each unit has base strength 1  
    – add +1 for every valid Support it receives  
    – the side with higher strength wins.
• If the defender loses, it is **dislodged** and must Retreat (or Disband)
  after adjudication.  A unit that is dislodged cannot give support this turn.
• If strengths are equal, all moves to that province fail (“bounce”).

─────────────────────────
ADDITIONAL CORE RULES
─────────────────────────
• RULE OF ONE – Only ONE unit may occupy a province, and each unit receives
  exactly ONE order per turn. A unit that gets no valid order H olds by default.

• ADJACENCY – A Move order must target a province ADJACENT to the unit’s
  current location.  Armies may enter only land provinces; Fleets may enter
  sea provinces or coastal land provinces whose coasts they touch.

• UNIT‑TYPE CONSISTENCY – Write the unit that is actually on the board:
  an Army cannot issue a Fleet order and vice versa.

• NO SELF‑DISLODGEMENT – A power may not dislodge its own unit nor cut
  support that is helping one of its own units.

• BOUNCES & SWAPS – If two units with equal strength enter the same province,
  both fail (“bounce”).  Units may not swap places except via Convoy.

• COASTAL DETAIL – Fleets must specify coast when REQUIRED and may not
  “coast‑crawl” (e.g., `F SPA/NC – SPA/SC` is illegal).

• CONVOYS (ADVANCED) – An Army ordered to “‑” a non‑adjacent coastal province
  is assumed to use any valid convoy route; add “via Convoy” if you require it.
  Every Fleet in the chain must write a separate Convoy order.

• RETREATS & DISBANDS – After combat, dislodged units must Retreat to an
  adjacent vacant province or Disband.  (Build & Disband orders are handled
  only in WINTER adjustment phases.)

─────────────────────────
COASTAL & SPLIT PROVINCES
─────────────────────────
• Specify coasts when needed:  
  `F STP/NC - BAR`   or   `F SPA/SC - WES`  
• Omit coast when unambiguous (e.g., "F BRE - MAO").

─────────────────────────
ILLEGAL ORDER EXAMPLES (auto‑rejected)
─────────────────────────
• `A PAR - PAR`       (Unit cannot move to its own province)  
• `F MUN - BER`       (Fleets may not enter land‑locked provinces)  
• `A ROM C A NAP - TUN`  (Armies cannot convoy; only Fleets can)

─────────────────────────
OUTPUT SPECIFICATION FOR THIS PROMPT
─────────────────────────
Return **exactly one JSON array** of strings—one string **per current unit location**.
Each string must be a properly‑formatted order as defined above.

Example ⟶   `["A PAR - BUR", "A MAR S A PAR - BUR", "F ENG C A LON - BEL"]`

NO extra keys, explanation, or text outside the JSON array.

##############################
""".strip()

_SYSTEM_LOADED: set[str] = set()

# ────────────────────────────────────────────────────────────────────────────
#  legal checks
# ────────────────────────────────────────────────────────────────────────────
def legal_orders_for(game: Game, power: str) -> set[str]:
    """Return the flat set of every order the engine itself considers legal
    for the current phase and the given power."""
    locs = game.get_orderable_locations(power)
    all_opts = game.get_all_possible_orders()     
    return {o for loc in locs for o in all_opts[loc]}

def filter_to_legal(game: Game, power: str, orders: list[str]) -> list[str]:
    """Keep only orders that appear in the engine‑generated legal set."""
    legal = legal_orders_for(game, power)
    return [o for o in orders if o in legal]



# --------------------------------------------------------------------------- #
#  Thin wrapper around `ollama run` so we can add --system the first time     #
# --------------------------------------------------------------------------- #
def run_ollama(model: str,
               prompt: str,
               system: Optional[str] = None,
               *,
               max_tokens: int = 256) -> str:
    """
    Send <prompt> to the Ollama daemon and return the raw text response.
    Requires `ollama serve` to be running on localhost:11434.
    """
    payload = {
        "model":   model,
        "prompt":  prompt,
        "system":  system or "",
        "stream":  False,
        "format": "json",
        "options": {"num_predict": max_tokens}
    }
    print(f"[REQ]  POST /api/generate  model={model}  "
          f"prompt_tokens≈{len(prompt.split())}")
    resp = requests.post("http://127.0.0.1:11434/api/generate", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["response"]

# --------------------------------------------------------------------------- #
#  Order normalisation                                                        #
# --------------------------------------------------------------------------- #
def _to_string_orders(raw: list) -> list[str]:
    """
    Convert any mixture of strings / {unit:action} dicts to a flat list of
    order strings like "A PAR - BUR".
    Unknown types are ignored.
    """
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item.strip())
        elif isinstance(item, dict):
            for unit, action in item.items():
                out.append(f"{unit} {action}".strip())
    return out

# --------------------------------------------------------------------------- #
#  Ollama helpers                                                              #
# --------------------------------------------------------------------------- #

def get_ollama_message(game: Game, power: str) -> str:
    """
    Ask the LLM to write a diplomatic message (full‑press) for the given power,
    choosing its own set of recipients.
    """
    model = MODEL_BY_POWER[power]
    phase = game.get_current_phase()
    other_powers = [p for p in game.powers if p != power]
    
    # Build past message history
    history = [f"{m['power']} → {m['recipients']}: {m['message']}" 
               for m in PHASE_MESSAGES.get(phase, [])
               if m['power'] == power or power in m['recipients']
            ]

        # ------------------------------------------------------------------ #
    #  PROMPT — either a valid JSON object *or* {} for silence           #
    # ------------------------------------------------------------------ #
        # ------------------------------------------------------------------ #
    #  PROMPT — either {} or ONE JSON object with meta                   #
    # ------------------------------------------------------------------ #
    prompt = (
        "★ IMPORTANT ★\n"
        "Reply with EITHER:\n"
        "  1) an EMPTY object: {}\n"
        "     – means you stay silent this round, OR\n"
        "  2) ONE valid JSON object with the keys below.\n"
        "ANY extra text will be discarded.\n\n"

        "JSON SCHEMA\n"
        "{\n"
        "  \"recipients\": [<one or more powers>],      # REQUIRED\n"
        "  \"message\":    \"<your plaintext message>\", # REQUIRED\n"
        "  \"meta\": {\n"
        "      \"intent\":     \"offer_alliance|request_support|threat|share_info|lie|other\",\n"
        "      \"trust\":      {<power>: float 0‑1, …},\n"
        "      \"confidence\": float 0‑1\n"
        "  }\n"
        "}\n\n"

        "Example:\n"
        "{\"recipients\":[\"FRANCE\"],\"message\":\"DMZ Ruhr?\",\n"
        " \"meta\":{\"intent\":\"offer_alliance\",\"trust\":{\"FRANCE\":0.6},\"confidence\":0.5}}\n\n"

        f"Phase: {phase} | You are {power}\n"
        f"Allowed recipients: {', '.join(other_powers)}\n"
        f"Past messages visible to you this phase:\n{json.dumps(history, indent=2)}\n\n"
        "Remember: send either {} or one JSON object—no markdown.\n"
    )

    raw_output = run_ollama(model, prompt, max_tokens=CHAT_TOKENS)

    match = re.search(r"\{.*?\}", raw_output, re.DOTALL)

    if not match:
        print(f"[{power}] No valid JSON object found in response.")
        print(f"[{power}] Raw output:\n{raw_output}\n")
        return ""


    try:
        result = json.loads(raw_output)
    except json.JSONDecodeError:

        m = re.search(r"\{.*?\}", raw_output, re.S)
        if not m:
            print(f"[{power}] No JSON found.")
            return ""
        try:
            result = json.loads(m.group())
        except json.JSONDecodeError:
            print(f"[{power}] Still invalid JSON; dropping message.")
            return ""

    recipients = result.get("recipients", [])
    msg = result.get("message", "").strip()
    meta = result.get("meta", {})
    if not recipients or not msg:
        return ""


    if phase not in PHASE_MESSAGES:
        PHASE_MESSAGES[phase] = []
    PHASE_MESSAGES[phase].append({
        "power": power,
        "recipients": recipients,
        "message": msg,
        "meta": meta
    })

    for rec in recipients:
        message = Message(
            sender=power,
            recipient=rec,
            message=msg,
            phase=phase,
            time_sent=int(time.time())
        )
        game.add_message(message)

    # Record for NLP
    DIALOGUE_LOG.append({
        "phase": phase,
        "power": power,
        "recipients": recipients,
        "type": "chat",
        "prompt": prompt,
        "response": msg,
        "meta": meta
    })

    return msg



def get_ollama_orders(game: Game, power: str) -> list[str]:
    """
    Ask the LLM for exactly one legal order per unit this power controls.
    """
    model = MODEL_BY_POWER[power]

    locs = game.get_orderable_locations(power)
    if not locs:
        return []

    all_opts = game.get_all_possible_orders()
    legal_flat = sorted({o for loc in locs for o in all_opts[loc]})

    # Send the full RULEBOOK only once per power
    system_text = None
    if power not in _SYSTEM_LOADED:
        system_text = RULEBOOK
        _SYSTEM_LOADED.add(power)

    prompt = (
        f"You are {power}. Phase {game.get_current_phase()}.\n"
        "Return ONE JSON array with exactly one legal order per unit.\n"
        "Legal orders:\n" + json.dumps(legal_flat)
    )

    output = run_ollama(model, prompt, system_text, max_tokens=ORDER_TOKENS)

    # Parse first JSON array in the response
    m = re.search(r"\[[^\]]*\]", output, re.S)
    if m:
        try:
            raw_orders = json.loads(output)
        except json.JSONDecodeError:
            raw_orders = []
    else:
        raw_orders = []

    # Keep only legal strings
    orders_raw = _to_string_orders(raw_orders)
    orders = filter_to_legal(game, power, orders_raw)

    # Back‑fill missing units with random legal orders
    if len(orders) < len(locs):
        missing = [
            random.choice(all_opts[loc])
            for loc in locs
            if not any(o.split()[1] == loc for o in orders)
        ]
        orders.extend(missing)

    # Log
    DIALOGUE_LOG.append({
        "phase": game.get_current_phase(),
        "power": power,
        "type": "orders",
        "prompt": prompt,
        "response": output,
        "orders": orders
    })

    return orders


# --------------------------------------------------------------------------- #
#  Main driver                                                                #
# --------------------------------------------------------------------------- #
def main():
    game = Game(map_name="standard")
    # enable full‑press messaging
    game.add_rule("PRESS")
    print("Started game", game.game_id, "with powers:", ", ".join(game.powers))

    while not game.is_game_done:
        phase = game.get_current_phase()
        year = int(phase[1:5])
        if year > MAX_YEAR:
            print(f"Reached {year}; stopping early.")
            break

        for _ in range(2):
            turn_order = list(game.powers)   # convert set → list
            random.shuffle(turn_order)       # in‑place shuffle
            for pw in turn_order:
                get_ollama_message(game, pw)

        for pw in game.powers:
            orders = get_ollama_orders(game, pw)
            safe_orders = filter_to_legal(game, pw, _to_string_orders(orders))
            game.set_orders(pw, safe_orders)


        phase_data = game.process()
        DIALOGUE_LOG.append({
            "phase": phase_data.name,
            "type": "engine_messages",
            "messages": [m.message for m in phase_data.messages.values()]
        })
        print("Processed", phase_data.name)
        time.sleep(0.05)

    # 4) Save results
    OUTPUT_FILE.write_text(json.dumps(to_saved_game_format(game), indent=2))
    DIALOGUE_FILE.write_text(json.dumps(DIALOGUE_LOG, indent=2))
    print("Game finished. Saved to", OUTPUT_FILE.resolve())
    print("Dialogue written to", DIALOGUE_FILE.resolve())

# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    main()