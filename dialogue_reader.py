
def render_dialogue_to_html(dialogue_log_path: str, output_path: str):
    import json

    with open(dialogue_log_path, "r") as f:
        dialogue = json.load(f)

    html = ['<html><head><style>']
    html.append("""
        body { font-family: sans-serif; padding: 1em; background: #f0f0f0; }
        .msg { background: white; padding: 1em; margin: 1em 0; border-radius: 8px; }
        .meta { font-size: 0.9em; color: #555; }
        .power { font-weight: bold; }
        pre { background: #eee; padding: 0.5em; border-radius: 4px; white-space: pre-wrap; }
    """)
    html.append('</style></head><body><h1>Diplomacy Chat Log</h1>')

    for entry in dialogue:
        if entry["type"] == "chat":
            html.append('<div class="msg">')
            html.append(f'<div class="meta"><span class="power">{entry["power"]}</span> during {entry["phase"]}</div>')
            html.append(f'<pre>{entry["response"]}</pre>')
            html.append('</div>')

    html.append('</body></html>')

    with open(output_path, "w") as f:
        f.write('\n'.join(html))

    print("Saved visualization to", output_path)

render_dialogue_to_html("dialogue_log.json", "dialogue_view.html")