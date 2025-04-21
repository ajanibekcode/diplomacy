import json
import re
from collections import defaultdict


def clean_prompt_text(prompt: str) -> str:
    prompt = re.sub(r"###\s*DIPLOMACY CHAT\s*###", "", prompt)
    prompt = re.sub(r"(?i)Phase:.*?\n", "", prompt)
    prompt = re.sub(r"(?i)You are the official representative.*?\n", "", prompt)
    prompt = re.sub(r"- The entire reply MUST.*?\n", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"(?i)Past messages.*?:\s*\[.*?\]\s*", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"\n{2,}", "\n", prompt)
    return prompt.strip()


def render_combined_log_to_html(dialogue_log_path: str, game_state_path: str, output_path: str):
    with open(dialogue_log_path, "r") as f:
        dialogue_log = json.load(f)

    with open(game_state_path, "r") as f:
        game_data = json.load(f)

    messages_by_phase = defaultdict(list)
    for entry in dialogue_log:
        if entry["type"] == "chat":
            messages_by_phase[entry["phase"]].append(entry)

    html = ['<html><head><style>']
    html.append("""
        body { font-family: sans-serif; padding: 1em; background: #f0f0f0; }
        .msg { background: white; padding: 1em; margin: 1em 0; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); }
        .meta { font-size: 0.9em; color: #555; margin-bottom: 0.5em; }
        .power { font-weight: bold; }
        .to { color: #0077aa; }
        pre { background: #f9f9f9; padding: 0.75em; border-radius: 4px; white-space: pre-wrap; }
        .section-title { font-size: 1.1em; margin-top: 2em; }
        .orders, .units { background: white; padding: 1em; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); margin-top: 1em; }
        .orders .power, .units .power { display: block; margin-top: 0.5em; }
        ul { padding-left: 1.2em; margin: 0.5em 0; }
    """)
    html.append('</style></head><body>')
    html.append('<h1>THE LOGS</h1>')

    for phase in game_data["phases"]:
        phase_name = phase["name"]

        # Messages
        for msg in messages_by_phase.get(phase_name, []):
            sender = msg["power"]
            recipients = ", ".join(msg.get("recipients", []))
            message_text = msg["response"].strip()

            html.append('<div class="msg">')
            html.append(f'<div class="meta"><span class="power">{sender}</span> during {phase_name} <span class="to">→ {recipients}</span></div>')
            html.append(f'<pre>{message_text}</pre>')
            html.append('</div>')

        # Orders (raw)
        html.append(f'<div class="section-title">Orders ({phase_name})</div>')
        html.append('<div class="orders">')
        for power, orders in phase.get("orders", {}).items():
            html.append(f'<span class="power">{power}:</span>')
            if orders:
                html.append('<ul>')
                for order in orders:
                    html.append(f'<li>{order}</li>')
                html.append('</ul>')
            else:
                html.append('<p><em>No orders submitted.</em></p>')
        html.append('</div>')

        # Units
        html.append(f'<div class="section-title">Units on the Board ({phase_name})</div>')
        html.append('<div class="units">')
        for power, units in phase["state"].get("units", {}).items():
            html.append(f'<span class="power">{power}:</span> {", ".join(units)}')
        html.append('</div>')

    html.append('</body></html>')

    with open(output_path, "w") as f:
        f.write('\n'.join(html))

    print(f"Saved combined game log to {output_path}")


# Run it
render_combined_log_to_html("dialogue_log.json", "game_state.json", "full_game_view.html")
