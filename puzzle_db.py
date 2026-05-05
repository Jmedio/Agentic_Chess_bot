import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chess_data.db")

# 0-2301 Doesn't include 2301+ ~
DIFFICULTY_BANDS = {
    "beginner":    (0,    1100),
    "intermediate":(1100, 1800),
    "advanced":    (1800, 2301),
}

def _db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def get_random_puzzle(
    difficulty: str = "intermediate",
    seen_ids: set | None = None,
    user_id: str = "default",
) -> dict | None:
    """
    Return one random unseen puzzle for the requested difficulty.

    Parameters
    ----------
    difficulty : 'beginner' | 'intermediate' | 'advanced'
    seen_ids   : set of puzzle IDs already shown this session (optional;
                 used as a fast in-memory pre-filter before the DB query)
    user_id    : key for the user_puzzle_history table

    Returns a dict with:
        puzzle_id, fen, solution_moves (list), rating, themes, difficulty
    Returns None if the DB is missing or no unseen puzzle is found.
    """
    if not os.path.exists(DB_PATH):
        return None

    seen = seen_ids or set()
    lo, hi = DIFFICULTY_BANDS.get(difficulty.lower(), (1100, 1800))

    conn = _db()
    c    = conn.cursor()

    # Exclude puzzles already seen in this session (in-memory set) AND
    # any stored in the persistent history table for this user_id.
    # Using ORDER BY RANDOM() LIMIT 1 is fine for our DB size.
    c.execute("""
        SELECT p.id, p.fen, p.solution_moves, p.rating, p.themes
        FROM puzzles p
        LEFT JOIN user_puzzle_history h
          ON p.id = h.puzzle_id AND h.user_id = ?
        WHERE p.difficulty = ?
          AND h.puzzle_id IS NULL
        ORDER BY RANDOM()
        LIMIT 1
    """, (user_id, difficulty.lower()))

    row = c.fetchone()

    if not row:
        conn.close()
        return None

    puzzle_id, fen, solution_str, rating, themes = row

    # Mark as shown before returning so a crash can't cause a repeat
    c.execute(
        "INSERT OR IGNORE INTO user_puzzle_history "
        "(user_id, puzzle_id, date_shown) VALUES (?, ?, datetime('now'))",
        (user_id, puzzle_id)
    )
    conn.commit()
    conn.close()

    return {
        "puzzle_id":      puzzle_id,
        "fen":            fen,
        "solution_moves": solution_str.split(),   # list of UCI strings e.g. ['e2e4','e7e5']
        "rating":         rating,
        "themes":         themes,
        "difficulty":     difficulty.lower(),
    }


def get_puzzle_counts() -> dict:
    """
    Return {difficulty: count} for all three bands.
    Useful for debugging or showing stats in the sidebar.
    """
    if not os.path.exists(DB_PATH):
        return {b: 0 for b in DIFFICULTY_BANDS}

    conn = _db()
    c    = conn.cursor()
    counts = {}
    for band in DIFFICULTY_BANDS:
        c.execute("SELECT COUNT(*) FROM puzzles WHERE difficulty = ?", (band,))
        counts[band] = c.fetchone()[0]
    conn.close()
    return counts


def mark_puzzle_solved(puzzle_id: str, user_id: str = "default") -> None:
    """Update the history record for a solved puzzle."""
    if not os.path.exists(DB_PATH):
        return
    conn = _db()
    c    = conn.cursor()
    c.execute(
        "UPDATE user_puzzle_history SET solved = 1 "
        "WHERE puzzle_id = ? AND user_id = ?",
        (puzzle_id, user_id)
    )
    conn.commit()
    conn.close()
