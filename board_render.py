"""
board_render.py — Lightweight chess board rendering helpers.
"""

import chess
import chess.svg


# Default colors. Blue marks the opponent's setup move at puzzle start;
# red marks the user's final move once the puzzle is solved.
ARROW_COLOR_BLUE = "#1976D2A6"   # New Transparent 35%
ARROW_COLOR_RED  = "#C62828A6"   # ^
#ARROW_COLOR_BLUE = "#1976D2"
#ARROW_COLOR_RED  = "#C62828"


def render_board_svg(
    fen: str,
    last_move_uci: str | None = None,
    arrow_uci: str | None = None,
    arrow_color: str = ARROW_COLOR_BLUE,
    size: int = 380,
    flipped: bool | None = None,
) -> str:
    """
    Build an SVG string for the position described by `fen`.

    Parameters
    ----------
    fen           : FEN to render
    last_move_uci : UCI move (e.g. 'e2e4') — yellow square highlight
    arrow_uci     : UCI move drawn as a colored arrow on the board
    arrow_color   : hex string for the arrow (default blue). Use
                    ARROW_COLOR_RED to mark the puzzle-final move.
    size          : pixel size (default 380)
    flipped       : explicit orientation override. None = auto-flip based
                    on whose turn it is in the FEN.
    """
    try:
        board = chess.Board(fen)
    except Exception:
        board = chess.Board()

    if flipped is None:
        flipped = (board.turn == chess.BLACK)

    last_move = None
    if last_move_uci:
        try:
            last_move = chess.Move.from_uci(last_move_uci)
        except Exception:
            last_move = None

    arrows: list = []
    if arrow_uci:
        try:
            mv = chess.Move.from_uci(arrow_uci)
            arrows.append(chess.svg.Arrow(mv.from_square, mv.to_square, color=arrow_color))
        except Exception:
            pass

    return chess.svg.board(
        board,
        lastmove=last_move,
        arrows=arrows,
        size=size,
        flipped=flipped,
    )


def push_moves(fen: str, uci_moves: list[str]) -> str:
    """Apply UCI moves in order; return resulting FEN."""
    try:
        board = chess.Board(fen)
    except Exception:
        return fen
    for uci in uci_moves:
        try:
            mv = chess.Move.from_uci(uci)
        except Exception:
            break
        if mv not in board.legal_moves:
            break
        board.push(mv)
    return board.fen()


def uci_to_san(fen: str, uci: str) -> str:
    """Convert a UCI move to readable SAN given the position FEN."""
    try:
        board = chess.Board(fen)
        return board.san(chess.Move.from_uci(uci))
    except Exception:
        return uci


def fen_turn_color(fen: str) -> str:
    """Return 'white' or 'black' for whose turn it is in this FEN."""
    try:
        return "white" if chess.Board(fen).turn == chess.WHITE else "black"
    except Exception:
        return "white"
