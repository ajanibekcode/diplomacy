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
OUTPUT_FILE  = Path("final_game_state.json")
DIALOGUE_FILE = Path("final_dialogue_log.json")
MAX_YEAR = 1912
DIALOGUE_LOG: list[dict] = []
PHASE_MESSAGES = {}

# optimize by reducing tokens
CHAT_TOKENS = 256
ORDER_TOKENS = 64

# Rule‑book appended to every prompt (≈ 140 tokens)
RULEBOOK = """
### DIPLOMACY – COMPLETE ORDER‑FORMAT REFERENCE (7‑Player Standard Map) ###

OBJECTIVE
• The goal is to control as many supply centers (SCs) as possible by the end of Winter 1912.
• The game ends after the **Winter 1912 Adjustment** phase (`W1912A`).
• The power with the most SCs is the winner.
• Strategic arc: build alliances in early years, position mid-game, and seize SCs in the endgame.

─────────────────────────
GENERAL
─────────────────────────
• Seven powers (AUS, ENG, FRA, GER, ITA, RUS, TUR) each control Armies (A) and/or Fleets (F).
• Each unit occupies **one province** (land or sea). Only one unit may occupy a province at a time.
• All orders are written using **three-letter province codes** (e.g., PAR, TYR, NTH).
• All players write orders simultaneously. Resolution (adjudication) follows standard Diplomacy rules.
• Players may form alliances, propose peace deals, offer support, or deceive and betray one another.
• There is no obligation to honor agreements. Trust must be earned — or exploited.

─────────────────────────
GAME PHASE STRUCTURE
─────────────────────────
The game progresses in discrete **phases** identified by a 6-character code:

  ┌──────────────┬────────────────────────┐
  │ Phase Code   │ Meaning                │
  ├──────────────┼────────────────────────┤
  │ S1901M       │ Spring 1901 Movement   │
  │ S1901R       │ Spring 1901 Retreat    │
  │ F1901M       │ Fall 1901 Movement     │
  │ F1901R       │ Fall 1901 Retreat      │
  │ W1901A       │ Winter 1901 Adjustment │
  └──────────────┴────────────────────────┘

• **M (Movement)**: Players issue orders for all units.
• **R (Retreat)**: Dislodged units must either retreat or disband.
• **A (Adjustment)**: Players build or disband units to match their SC count.

─────────────────────────
ORDER FORMATS BY PHASE
─────────────────────────

▌ MOVEMENT PHASES (`M`)
────────────────────────────
1. **Hold**  
   `A PAR H` — Unit holds position.

2. **Move**  
   `A PAR - BUR` — Army moves to Burgundy.

3. **Support**  
   `A MUN S A PAR - BUR` — Army in Munich supports Army PAR → BUR.

4. **Convoy**  
   `F ENG C A LON - BEL` — Fleet convoys Army from London to Belgium.

▌ RETREAT PHASES (`R`)
────────────────────────────
• Only dislodged units receive orders during Retreats.
• Retreat to an adjacent, empty province or disband.

1. **Retreat**  
   `A BEL R HOL` — Army in Belgium retreats to Holland.

2. **Disband**  
   `F TUN D` — Fleet in Tunis disbands.

Rules:
• Cannot retreat into the province that dislodged the unit.
• Cannot retreat to occupied or contested provinces.

▌ ADJUSTMENT PHASES (`A`)
────────────────────────────
• Occurs only during **Winter**.
• Based on the number of SCs a power owns:
   – If SCs > units: **Build** new units.
   – If SCs < units: **Disband** excess units.

1. **Build Army**  
   `Build A PAR` — Build Army in Paris.

2. **Build Fleet**  
   `Build F LON` — Build Fleet in London.

3. **Disband Unit**  
   `Disband A TUN` — Disband Army in Tunis.

Rules:
• Builds must be placed in **vacant home supply centers**.
• Disbands can target any owned unit.

─────────────────────────
ADDITIONAL CORE RULES
─────────────────────────
• **Simultaneous Orders** — All players submit orders at the same time.
• **Bounce** — Equal strength moves to the same province fail.
• **Support Cutting** — A supporting unit is disrupted if attacked from any province **except the one it's supporting**.
• **No Self-Dislodgement** — A power may not dislodge its own unit or cut its own support.
• **No Swaps** — Units may not swap places without a convoy chain.
• **Coastal Details** — Fleets must specify coast (e.g., SPA/NC vs SPA/SC) when ambiguous.

─────────────────────────
COASTAL & SPLIT PROVINCES
─────────────────────────
• Some provinces have multiple coasts (e.g., SPA, STP) requiring coast specifiers:
   `F MAO - SPA/SC` — Fleet moves to Spain, South Coast.
   `F STP/NC - BAR` — Fleet in St. Petersburg (North Coast) moves to Barents Sea.

• Omit coast when unambiguous:
   `F BRE - MAO` — Valid, only one coast.

─────────────────────────
ILLEGAL ORDER EXAMPLES (auto-rejected)
─────────────────────────
• `A PAR - PAR` — Cannot move to own province.
• `F MUN - BER` — Fleets may not enter landlocked provinces.
• `F SPA/NC - SPA/SC` — Cannot move across coasts of same province.
• `A ROM C A NAP - TUN` — Armies cannot convoy; only fleets can.
• `Build A ROM` — Invalid if ROM is occupied or not a home SC.

─────────────────────────
OUTPUT FORMAT FOR THIS PROMPT
─────────────────────────
Return a **single JSON array** of strings — one valid order per controllable unit.

Example:
`["A PAR - BUR", "F ENG C A LON - BEL", "A MUN S A PAR - BUR"]`

Do not include any extra text, explanation, keys, or markdown. Only the raw JSON array.

##############################
""".strip()

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
    # if this is one of the chat models, wrap system+user
   
    payload = {
        "model": model,
        "system": system or "",
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"num_predict": max_tokens}
    }
    print(f"[REQ] POST /api/generate model={model} prompt_tokens≈{len(prompt.split())}")
    return _ollama_call(payload)

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


# ----------------------------------------------------------------------- #
#  ONE global Session so HTTP keep‑alive works                            #
# ----------------------------------------------------------------------- #
_SESSION = requests.Session()

def _ollama_call(payload: dict) -> str:
    """Blocking POST that returns the model's raw response string."""
    r = _SESSION.post("http://127.0.0.1:11434/api/generate",
                      json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["response"]


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

    prompt = (
        "STRATEGIC MESSAGE REQUIRED\n"
        "Reply with ONE valid JSON object using the format below.\n"
        "The objective is to control as many supply centers as possible by the end of 1912.\n"
        "Use the current year and phase to guide your tone and tactics — build trust early, position mid-game, and act boldly near the end.\n\n"
        "Your message should reflect your strategy: alliances, threats, deception, or intel sharing.\n"
        "No markdown, no extra text — only the JSON.\n\n"

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

        "★ EXAMPLES ★\n"
        "{\n"
        "  \"recipients\": [\"FRANCE\"],\n"
        "  \"message\": \"DMZ Ruhr?\",\n"
        "  \"meta\": {\n"
        "    \"intent\": \"offer_alliance\",\n"
        "    \"trust\": {\"FRANCE\": 0.6},\n"
        "    \"confidence\": 0.5\n"
        "  }\n"
        "}\n\n"
        "{\n"
        "  \"recipients\": [\"GERMANY\"],\n"
        "  \"message\": \"Withdraw from Denmark or face consequences.\",\n"
        "  \"meta\": {\n"
        "    \"intent\": \"threat\",\n"
        "    \"trust\": {\"GERMANY\": 0.2},\n"
        "    \"confidence\": 0.8\n"
        "  }\n"
        "}\n\n"
        "{\n"
        "  \"recipients\": [\"ITALY\"],\n"
        "  \"message\": \"I’m moving into Tyrolia to protect you from Austria.\",\n"
        "  \"meta\": {\n"
        "    \"intent\": \"lie\",\n"
        "    \"trust\": {\"ITALY\": 0.9},\n"
        "    \"confidence\": 0.7\n"
        "  }\n"
        "}\n\n"
        "{\n"
        "  \"recipients\": [\"TURKEY\"],\n"
        "  \"message\": \"Austria is planning to attack you next turn.\",\n"
        "  \"meta\": {\n"
        "    \"intent\": \"share_info\",\n"
        "    \"trust\": {\"TURKEY\": 0.5},\n"
        "    \"confidence\": 0.6\n"
        "  }\n"
        "}\n\n"

        f"Phase: {phase} | Year: {phase[1:5]} | You are {power}\n"
        f"Allowed recipients: {', '.join(other_powers)}\n"
        f"Past messages visible to you this phase:\n{json.dumps(history, indent=2)}\n\n"
        "Now respond with ONE valid JSON object as above.\n"
    )

    system_text = RULEBOOK
    raw_output = run_ollama(model, prompt, system_text)

    match = re.search(r"\{.*\}", raw_output, re.DOTALL)

    if not match:
        print(f"[{power}] No valid JSON object found in response.")
        print(f"[{power}] Raw output:\n{raw_output}\n")
        return ""


    try:
        result = json.loads(raw_output)
    except json.JSONDecodeError:

        m = re.search(r"\{.*\}", raw_output, re.S)
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
    system_text = RULEBOOK

    prompt = (
        f"You are {power}. Phase {game.get_current_phase()}.\n"
        "Return ONLY a valid JSON array of orders, e.g.:\n"
        "[\"A PAR - BUR\", \"F LON H\"]\n\n"
        "You MUST use square brackets and quote each order string.\n"
        "Format: [\"order1\", \"order2\", ...]\n\n"
        "No explanation, no extra text, no object keys.\n\n"
        "Choose from these legal orders:\n" + json.dumps(legal_flat)
    )

    output = run_ollama(model, prompt, system_text)

    # print(f"\n==== [{power}] — PHASE {game.get_current_phase()} ====")
    # print("Raw model output:\n", output)

    # Extract all possible orders from the response
    extracted_orders = []
    
    # Pattern to match valid orders - more lenient to catch variations
    order_pattern = r'["\']?(A|F)\s+[A-Z]{3}(\s*-\s*[A-Z]{3}|\s+[HSRDC]|\s+S\s+[AF]\s+[A-Z]{3}(\s*-\s*[A-Z]{3})?)["\']?'
    
    # First try to parse as JSON
    try:
        # Try direct JSON parsing
        parsed = json.loads(output.strip())
        
        # Function to recursively extract orders from any nested structure
        def extract_from_structure(item):
            orders = []
            if isinstance(item, str):
                # Check if string matches order pattern
                if re.match(order_pattern, item.strip()):
                    orders.append(item.strip())
            elif isinstance(item, list):
                # Extract from each list item
                for subitem in item:
                    orders.extend(extract_from_structure(subitem))
            elif isinstance(item, dict):
                # Extract from both keys and values
                for key, value in item.items():
                    # Check if key is an order
                    if isinstance(key, str) and re.match(order_pattern, key.strip()):
                        orders.append(key.strip())
                    # Check value too
                    orders.extend(extract_from_structure(value))
            return orders
        
        extracted_orders = extract_from_structure(parsed)
        
        # if extracted_orders:
        #     print(f"[{power}] Extracted orders from parsed JSON structure: {extracted_orders}")
        
    except json.JSONDecodeError:
        # If not valid JSON, try regex extraction
        pass
    
    # If no orders extracted from JSON structure, try regex patterns
    if not extracted_orders:
        # Look for JSON arrays with regex
        array_match = re.search(r'\[(.*?)\]', output, re.DOTALL)
        if array_match:
            array_content = array_match.group(1)
            try:
                # Try to parse the extracted array
                array_items = json.loads('[' + array_content + ']')
                for item in array_items:
                    if isinstance(item, str) and re.match(order_pattern, item.strip()):
                        extracted_orders.append(item.strip())
            except json.JSONDecodeError:
                # If array JSON parsing fails, extract with regex
                pass
    
    # Last resort: direct regex matching of order patterns in the raw output
    if not extracted_orders:
        # Find all matches of the order pattern
        matches = re.findall(order_pattern, output)
        if matches:
            # Extract the full text of each match
            full_matches = []
            for match_tuple in matches:
                # Find the original match in the text
                start_idx = 0
                while True:
                    unit_type = match_tuple[0]  # A or F
                    potential_start = output.find(unit_type, start_idx)
                    if potential_start == -1:
                        break
                    
                    # Extract a generous window around the match
                    window = output[potential_start:potential_start+30]
                    if re.match(order_pattern, window):
                        # Clean up the order text
                        order = re.match(order_pattern, window).group(0)
                        order = order.strip().strip('"\'')
                        full_matches.append(order)
                        break
                    
                    start_idx = potential_start + 1
            
            extracted_orders.extend(full_matches)
            # print(f"[{power}] Extracted orders with pattern matching: {extracted_orders}")
    
    # Normalize and deduplicate orders
    orders_raw = []
    for order in extracted_orders:
        # Clean up formatting issues
        clean_order = order.strip().strip('"\'')
        # Remove duplicate whitespace
        clean_order = re.sub(r'\s+', ' ', clean_order)
        if clean_order not in orders_raw:
            orders_raw.append(clean_order)
    
    # print(f"[{power}] Parsed orders from model: {orders_raw}")

    # Filter to only legal orders
    orders = filter_to_legal(game, power, orders_raw)
    # print(f"[{power}] Filtered legal orders: {orders}")
    
    # Back-fill any missing or illegal orders with HOLD
    assigned = {o.split()[1] for o in orders}
    
    for loc in locs:
        if loc not in assigned:
            # Get first legal order to infer unit type (A or F)
            example_order = all_opts[loc][0]
            unit_type = example_order.split()[0]
            orders.append(f"{unit_type} {loc} H")
    
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

    # 4) Save results
    OUTPUT_FILE.write_text(json.dumps(to_saved_game_format(game), indent=2))
    DIALOGUE_FILE.write_text(json.dumps(DIALOGUE_LOG, indent=2))
    print("Game finished. Saved to", OUTPUT_FILE.resolve())
    print("Dialogue written to", DIALOGUE_FILE.resolve())

# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    main()