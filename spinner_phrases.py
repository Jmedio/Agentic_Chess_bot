"""
spinner_phrases.py — Mode-aware loading-phrase bank for st.spinner().
"""

import random

SPINNER_PHRASES: dict[str, dict[str, list[str]]] = {

    "Chess Puzzle": {
        "MOVE": [
            "Verifying the tactic...",
            "Checking your move against the solution...",
            "Comparing to the engine line...",
            "Looking deep into the endgame...",
            "Calculating grandmaster variations...",
        ],
        "CONVERSATION": [
            "Crafting a useful nudge...",
            "Looking for a gentle clue...",
            "Pondering the right hint...",
            "Searching for the key idea...",
        ],
        "ILLEGAL_MOVE": [
            "Hmm, double-checking that one...",
            "Let me re-read the board...",
        ],
        "UNREADABLE": [
            "Trying to parse that...",
            "Decoding your notation...",
        ],
        "default": [
            "Legal Danish is analyzing the position...",
            "Evaluating pawn structures...",
            "Checking for sneaky knight forks...",
        ],
    },

    "Analysis Mode": {
        "MOVE": [
            "Asking the engine...",
            "Running deep analysis...",
            "Counting material and weighing imbalances...",
            "Evaluating the position to centipawn precision...",
            "Tracing the principal variation...",
        ],
        "CONVERSATION": [
            "Consulting Stockfish...",
            "Breaking down the position...",
            "Scanning for tactical patterns...",
        ],
        "ILLEGAL_MOVE": [
            "Hmm, that doesn't look right...",
        ],
        "UNREADABLE": [
            "Decoding your notation...",
        ],
        "default": [
            "Consulting Stockfish...",
            "Measuring king safety...",
            "Calculating deep variations...",
        ],
    },

    "Opening Practice": {
        "MOVE": [
            "Comparing to the main line...",
            "Checking ECO theory...",
            "Reviewing transpositions...",
        ],
        "CONVERSATION": [
            "Recalling opening ideas...",
            "Flipping through grandmaster preparation...",
        ],
        "default": [
            "Tracing the opening repertoire...",
            "Opening the theory book...",
        ],
    },
}


def pick_spinner(mode: str, input_type: str | None = None) -> str:
    mode_dict = SPINNER_PHRASES.get(mode) or SPINNER_PHRASES["Chess Puzzle"]
    if input_type and input_type in mode_dict and mode_dict[input_type]:
        return random.choice(mode_dict[input_type])
    if "default" in mode_dict and mode_dict["default"]:
        return random.choice(mode_dict["default"])
    return random.choice(SPINNER_PHRASES["Chess Puzzle"]["default"])
