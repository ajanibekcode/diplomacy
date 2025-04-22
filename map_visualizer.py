import json
from pathlib import Path
from diplomacy import Game

def render_game_phases(saved_game_path: str, output_dir: str):
    # Load the saved game
    with open(saved_game_path, "r") as f:
        saved = json.load(f)

    # Create output directory
    out = Path(output_dir)
    out.mkdir(exist_ok=True)

    # Start a fresh game
    game = Game(map_name=saved["map"])
    game.add_rule("PRESS")

    for idx, phase in enumerate(saved["phases"]):
        phase_name = phase["name"]
        orders = phase.get("orders", {})

        # Set orders
        for power, unit_orders in orders.items():
            game.set_orders(power, unit_orders)

        # Render post-orders (with arrows)
        svg = game.render(incl_orders=True, incl_abbrev=True)
        with open(out / f"{idx:02d}_{phase_name}.svg", "w") as f:
            f.write(svg)
        
        game.process()

    print(f"\nAll SVG maps saved to '{output_dir}'")

render_game_phases("test_normal_game_state.json", "map_images")
