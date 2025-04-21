import json
from pathlib import Path
from diplomacy import Game

def render_game_phases(saved_game_path: str, output_dir: str):
    # 1) Load the saved game
    with open(saved_game_path, "r") as f:
        saved = json.load(f)

    # 2) Prepare output folder
    out = Path(output_dir)
    out.mkdir(exist_ok=True)

    # 3) Start a fresh Game
    game = Game(map_name=saved["map"])
    game.add_rule("PRESS")

    # 4) For each phase in your saved data:
    for idx, phase in enumerate(saved["phases"]):
        phase_name = phase["name"]
        orders = phase.get("orders", {})

        # --- RENDER BEFORE processing orders ---
        svg = game.render()  
        svg_path = out / f"{idx:02d}_{phase_name}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        print(f"Saved PRE‑{phase_name}: {svg_path}")

        # 5) Now feed in that phase’s orders
        for power, unit_orders in orders.items():
            game.set_orders(power, unit_orders)

        # 6) Process to advance to next phase
        game.process()

    print(f"\n✅ All SVG maps saved to '{output_dir}'")


render_game_phases("test_game.json", "map_images")