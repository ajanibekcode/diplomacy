import json
from collections import defaultdict, Counter
import pandas as pd
import re

# Load your data
with open('extreme_game_state.json') as f:
    game_state = json.load(f)

with open('extreme_dialogue_log.json') as f:
    dialogue_log = json.load(f)

# Helper function to extract support orders
def extract_support_targets(orders):
    """
    Returns a set of (unit, target) tuples from support orders.
    Example: 'A BUD S A VIE - TRI' => ('A VIE', 'TRI')
    """
    support_actions = set()
    for order in orders:
        if " S " in order:
            match = re.search(r'([AF] \S+) S ([AF] \S+)(?: - (\S+))?', order)
            if match:
                unit_supported = match.group(2)
                target = match.group(3) if match.group(3) else None
                support_actions.add((unit_supported, target))
    return support_actions

# Helper function to extract hostile move orders (non-support moves)
def extract_moves_against(orders):
    """
    Extracts destination provinces from move orders that are not support actions.
    For example: 'A BUD - GAL' would return 'GAL'.
    """
    move_targets = set()
    for order in orders:
        # Exclude support orders
        if " - " in order and " S " not in order:
            parts = order.split(" - ")
            if len(parts) == 2:
                target = parts[1].strip()
                move_targets.add(target)
    return move_targets

# Collect message and order data
message_data = defaultdict(lambda: {
    "sent": 0,
    "trust_values": [],
    "intent_counts": Counter(),
    "offers": [],
    "actual_orders": defaultdict(list)
})

# Process dialogue log
for entry in dialogue_log:
    if entry.get("type") == "chat":
        power = entry["power"]
        meta = entry.get("meta", {})
        trust_dict = meta.get("trust", {})
        intent = meta.get("intent", "other")
        message_data[power]["sent"] += 1
        message_data[power]["trust_values"].extend(trust_dict.values())
        message_data[power]["intent_counts"][intent] += 1

        if intent in ["offer_alliance", "request_support"]:
            message_data[power]["offers"].append({
                "phase": entry["phase"],
                "recipients": tuple(entry["recipients"]),
                "intent": intent
            })

# Process actual orders per phase
for phase in game_state.get("phases", []):
    phase_name = phase["name"]
    orders = phase.get("orders", {})
    for power, order_list in orders.items():
        message_data[power]["actual_orders"][phase_name] = order_list

# Analyze metrics with improved logic for success, neutrality, and betrayal.
metrics = []
for power, stats in message_data.items():
    trust_avg = sum(stats["trust_values"]) / len(stats["trust_values"]) if stats["trust_values"] else 0
    most_common_intent = stats["intent_counts"].most_common(1)[0][0] if stats["intent_counts"] else "other"

    successful_offers = 0
    betrayals = 0
    neutral_offers = 0

    for offer in stats["offers"]:
        intent = offer["intent"]
        
        # Count any declared lie as a betrayal
        if intent == "lie":
            betrayals += 1
            continue
        phase_orders = stats["actual_orders"].get(offer["phase"], [])
        support_actions = extract_support_targets(phase_orders)
        move_targets = extract_moves_against(phase_orders)

        helped = False
        attacked = False

        # Check if the offer was supported.
        for recipient in offer["recipients"]:
            for supported_unit, _ in support_actions:
                # Here we use a crude matching based on the initial letter.
                # Customize this logic as needed.
                if recipient[:1] == supported_unit[0]:
                    helped = True

            # Check if an order moved a unit into one of the recipient's home centers.
            recipient_centers = {}
            for p in game_state["phases"]:
                if p["name"] == offer["phase"]:
                    recipient_centers = set(p["state"]["centers"].get(recipient, []))
                    break

            # Betrayal if moving into recipient’s territory
            if any(target in recipient_centers for target in move_targets):
                attacked = True

            # Extra betrayal logic: supporting an attack on recipient
            for order in phase_orders:
                if " S " in order and any(recipient in order for recipient in offer["recipients"]):
                    # crude way to check if supporting an attack *on* the recipient
                    if " - " in order:  # support for move
                        attacked = True
        if helped:
            successful_offers += 1
        elif attacked:
            betrayals += 1
        else:
            neutral_offers += 1

    total_offers = len(stats["offers"])
    negotiation_success_rate = successful_offers / total_offers if total_offers else 0
    # Honesty rate here is calculated as the ratio of successful offers to the sum of successful and betrayed offers.
    backstab_rate = betrayals / total_offers if total_offers else 0
    honesty_rate = 1 - backstab_rate if total_offers else 0


    metrics.append({
        "power": power,
        "avg_trust": round(trust_avg, 2),
        "most_common_intent": most_common_intent,
        "negotiation_success_rate": round(negotiation_success_rate, 2),
        "honesty_rate": round(honesty_rate, 2),
    })

df = pd.DataFrame(metrics)
print(df)
