import json
import random
import re
import subprocess
import time
from pathlib import Path

from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format
from diplomacy.engine.message import Message

# --------------------------------------------------------------------------- #
#  Configuration                                                              #
# --------------------------------------------------------------------------- #
OLLAMA_MODEL = "mistral:7b-instruct"     
OUTPUT_FILE  = Path("test_game.json")
DIALOGUE_FILE = Path("dialogue_log.json")
MAX_YEAR = 1903
DIALOGUE_LOG: list[dict] = []
PHASE_MESSAGES = {}
# Rule‑book appended to every prompt (≈ 140 tokens)
RULEBOOK = """
### DIPLOMACY – COMPLETE ORDER‑FORMAT REFERENCE (7‑Player Standard Map) ###

GENERAL
• Seven powers (AUS, ENG, FRA, GER, ITA, RUS, TUR) each control Armies (A) and/or Fleets (F).
• Each unit occupies ONE province (land or sea).  Only one unit may occupy a province at a time.
• All orders are written in **three‑letter province abbreviations** (e.g., PAR, TYR, NTH).
• All players write orders simultaneously; adjudication follows standard Diplomacy rules
  (strength = 1 + number of valid supports, supports can be cut, self‑bounce possible, etc.).

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


# --------------------------------------------------------------------------- #
#  Ollama helpers                                                              #
# --------------------------------------------------------------------------- #

def get_ollama_message(game: Game, power: str) -> str:
    """
    Ask the LLM to write a diplomatic message (full‑press) for the given power.
    """
    phase = game.get_current_phase()
    # Collect past messages in this phase
    other_powers = [p for p in game.powers if p != power]
    recipients_text = ", ".join(other_powers)

    # Build history for this phase
    history = [
        f"{m['power']}: {m['message']}" for m in PHASE_MESSAGES.get(phase, [])
    ]

    prompt = (
        f"### DIPLOMACY CHAT ###\n"
        f"Phase: {phase}\n"
        f"You are the official representative of {power}. "
        f"Write a message addressed to the other powers: {recipients_text}.\n"
        f"Past messages:\n{json.dumps(history, indent=2)}\n\n"
        f"You are {power}. Write your next negotiation message (or leave empty if none):"
    )
    proc = subprocess.run(
        ["ollama", "run", OLLAMA_MODEL, prompt],
        capture_output=True, text=True
    )
    msg = (proc.stdout or proc.stderr).strip()
    # Record for NLP
    DIALOGUE_LOG.append({
        "phase": phase,
        "power": power,
        "type": "chat",
        "prompt": prompt,
        "response": msg
    })
    if phase not in PHASE_MESSAGES:
        PHASE_MESSAGES[phase] = []
    PHASE_MESSAGES[phase].append({
        "power": power,
        "message": msg
    })

    message = Message(
        sender=power,
        recipient="GLOBAL",
        message=msg,
        phase=phase,
        time_sent=int(time.time())
    )
    game.add_message(message)
    return msg



def get_ollama_orders(game: Game, power: str) -> list[str]:
    """
    Ask the LLM for exactly one legal order per unit this power controls.
    """
    locs = game.get_orderable_locations(power)
    if not locs:
        return []

    all_options = game.get_all_possible_orders()
    legal_dict = {loc: all_options[loc] for loc in locs}
    prompt = (
        f"{RULEBOOK}\n"
        f"# You are {power}. Current phase: {game.get_current_phase()}\n"
        "Legal orders for your units:\n"
        f"{json.dumps(legal_dict, indent=2)}\n\n"
        "BEGIN JSON ARRAY NOW:"
    )
    proc = subprocess.run(
        ["ollama", "run", OLLAMA_MODEL, prompt],
        capture_output=True, text=True
    )
    output = (proc.stdout or proc.stderr).strip()
    record = {
        "phase": game.get_current_phase(),
        "power": power,
        "type": "orders",
        "prompt": prompt,
        "response": output,
        "orders": None
    }
    # Extract first JSON array
    m = re.search(r"\[[^\]]*\]", output, re.S)
    if m:
        try:
            record["orders"] = orders = json.loads(m.group())
            DIALOGUE_LOG.append(record)
            return orders
        except json.JSONDecodeError:
            print(f"[{power}] JSON decode error; falling back to random.")
    else:
        print(f"[{power}] No JSON found; falling back to random.")
    # Fallback: random legal orders
    fallback = [random.choice(all_options[loc]) for loc in locs]
    record["orders"] = fallback
    DIALOGUE_LOG.append(record)
    return fallback


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

        # 1) Collect diplomatic messages
        for pw in game.powers:
            msg = get_ollama_message(game, pw)
            if msg:
                # Create message using the Message class
                message = Message(
                    sender=pw,
                    recipient='GLOBAL',
                    message=msg,
                    phase=phase,
                    time_sent=int(time.time())
                )
                game.add_message(message)

        # 2) Collect and set orders
        for pw in game.powers:
            orders = get_ollama_orders(game, pw)
            game.set_orders(pw, orders)

        # 3) Process and record engine messages
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