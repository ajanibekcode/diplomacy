import json
import re
from collections import defaultdict


# ------------------------------------------------------------
#  Helpers
# ------------------------------------------------------------
def clean_prompt_text(prompt: str) -> str:
    prompt = re.sub(r"###\s*DIPLOMACY CHAT\s*###", "", prompt)
    prompt = re.sub(r"(?i)Phase:.*?\n", "", prompt)
    prompt = re.sub(r"(?i)You are the official representative.*?\n", "", prompt)
    prompt = re.sub(r"- The entire reply MUST.*?\n", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"(?i)Past messages.*?:\s*\[.*?\]\s*", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"\n{2,}", "\n", prompt)
    return prompt.strip()


# ------------------------------------------------------------
#  Main renderer
# ------------------------------------------------------------
def render_combined_log_to_html(dialogue_log_path: str,
                               game_state_path: str,
                               output_path: str):
    with open(dialogue_log_path) as f:
        dialogue_log = json.load(f)
    with open(game_state_path) as f:
        game_data = json.load(f)

    # Summarize meta
    from collections import defaultdict
    intents = defaultdict(int)
    confidences = []
    for entry in dialogue_log:
        if entry.get("type")=="chat" and "meta" in entry:
            intent = entry["meta"].get("intent","unknown")
            intents[intent] += 1
            if "confidence" in entry["meta"]:
                confidences.append(entry["meta"]["confidence"])

    # Bucket messages by phase
    from collections import defaultdict
    messages_by_phase = defaultdict(list)
    for entry in dialogue_log:
        if entry["type"]=="chat":
            messages_by_phase[entry["phase"]].append(entry)

    html = ['<html><head><style>…your styles here…</style></head><body>']
    html.append('<h1>THE LOGS</h1>')

    # Meta summary
    html.append('<h2>Meta summary</h2><ul>')
    for intent,count in intents.items():
        html.append(f'<li><strong>{intent}</strong>: {count}</li>')
    avg_conf = sum(confidences)/len(confidences) if confidences else 0
    html.append(f'<li><strong>Avg confidence</strong>: {avg_conf:.2f}</li>')
    html.append('</ul>')

    # Now the per‑phase render (threads, orders, units) …
    for phase in game_data["phases"]:
        phase_name = phase["name"]

        # group chat into threads
        threads = defaultdict(list)
        for msg in messages_by_phase.get(phase_name, []):
            participants = frozenset([msg["power"], *msg.get("recipients",[])])
            threads[participants].append(msg)

        # render each thread
        for participants, msgs in threads.items():
            html.append(f'<div class="thread"><div class="meta">'
                        f'Thread: {", ".join(sorted(participants))}</div>')
            for msg in msgs:
                sender     = msg["power"]
                recipients = ", ".join(msg.get("recipients",[])) or "ALL"
                text       = msg["response"].strip()
                html.append('<div class="msg">')
                html.append(f'<div class="meta"><span class="power">{sender}'
                            f'</span> → {recipients} ({phase_name})</div>')
                html.append(f'<pre>{text}</pre>')

                # ** here is the meta display **
                meta = msg.get("meta", {})
                if meta:
                    intent     = meta.get("intent", "")
                    trust_map  = ", ".join(f"{p}:{t:.2f}"
                                           for p,t in meta.get("trust",{}).items())
                    confidence = meta.get("confidence","")
                    html.append('<div class="meta">')
                    html.append(f'  <strong>Intent:</strong> {intent}  &nbsp; '
                                f'<strong>Trust:</strong> {trust_map}  &nbsp; '
                                f'<strong>Conf:</strong> {confidence}')
                    html.append('</div>')

                html.append('</div>')  # close .msg
            html.append('</div>')      # close .thread

        # … then orders and units as before …

    html.append('</body></html>')
    with open(output_path,"w") as f:
        f.write("\n".join(html))
    print(f"Saved combined game log to {output_path}")

# ------------------------------------------------------------
#  Run it
# ------------------------------------------------------------
render_combined_log_to_html("final_dialogue_log.json",
                            "final_game_state.json",
                            "game_view.html")
