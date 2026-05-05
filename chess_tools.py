"""
chess_tools.py — LangChain tools + Stockfish analysis + tactic heuristics

SIGN CONVENTION: All evaluations use ABSOLUTE scoring.
  Positive = White advantage, Negative = Black advantage.

CLASSIFICATION: classify_input returns one of:
  MOVE          — legal move, parsed successfully
  ILLEGAL_MOVE  — looks like chess notation but not legal in position
  CONVERSATION  — not a move attempt (chat, questions, commands)
  (UNREADABLE was merged into ILLEGAL_MOVE — any notation-like input
   that doesn't parse is treated as "not legal here")
"""

import sys, os, re, json, atexit
import chess, chess.engine
from langchain.tools import tool

# RAG for openings — graceful degradation if not installed/built
try:
    from openings_rag import search_openings, format_results_for_agent
    _RAG_AVAILABLE = True
except ImportError:
    _RAG_AVAILABLE = False
    def search_openings(q, k=5): return []
    def format_results_for_agent(r): return "Opening knowledge base not available."

import shutil

def _find_stockfish() -> str:
    _os = sys.platform
    if _os == "win32":
        local = os.path.abspath("engines/wind_fish/stockfish/stockfish-windows-x86-64-avx2.exe")
    elif _os == "darwin":
        local = os.path.abspath("engines/mac_fish/stockfish/stockfish-macos-m1-apple-silicon")
    else:
        local = os.path.abspath("engines/linux_fish/stockfish/stockfish-ubuntu-x86-64")

    if os.path.exists(local):
        return local

    system = shutil.which("stockfish")
    if system:
        return system

    raise FileNotFoundError(
        "Stockfish not found. For local dev, place the binary in engines/. "
        "For Streamlit Cloud, add 'stockfish' to packages.txt."
    )

STOCKFISH_PATH = _find_stockfish()

PIECE_VALUES = {
    chess.PAWN:1, chess.KNIGHT:3, chess.BISHOP:3,
    chess.ROOK:5, chess.QUEEN:9, chess.KING:100,
}

# ═══ ENGINE ═══════════════════════════════════════════════════════════════════

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH); return _engine
    try: _engine.ping()
    except:
        try: _engine.quit()
        except: pass
        _engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    return _engine

atexit.register(lambda: (_engine.quit() if _engine else None))


# ═══ SCORING HELPERS ═════════════════════════════════════════════════════════

def _relative_to_absolute(cp, white_to_move):
    if cp is None: return 0
    return cp if white_to_move else -cp

def _format_pawns(abs_cp):
    if abs_cp is None: return "0.0"
    p = round(abs_cp / 100, 1)
    return f"+{p}" if p > 0 else ("0.0" if p == 0 else f"{p}")

def _eval_description(abs_cp):
    if abs_cp is None: return "unclear"
    p = abs(abs_cp) / 100
    side = "White" if abs_cp > 0 else "Black"
    if p < 0.3:  return "roughly equal"
    if p < 1.0:  return f"slight {side} advantage ({_format_pawns(abs_cp)})"
    if p < 3.0:  return f"clear {side} advantage ({_format_pawns(abs_cp)})"
    if p < 5.0:  return f"winning {side} advantage ({_format_pawns(abs_cp)})"
    return f"decisive {side} advantage ({_format_pawns(abs_cp)})"

def _cp_label(cp_loss):
    if cp_loss <= 10:  return "Best"
    if cp_loss <= 30:  return "Excellent"
    if cp_loss <= 90:  return "Good"
    if cp_loss <= 200: return "Inaccuracy"
    if cp_loss <= 400: return "Mistake"
    return "Blunder"


# ═══ TACTIC HEURISTICS ═══════════════════════════════════════════════════════

def _detect_fork(board, move):
    ba = board.copy(); ba.push(move)
    pc = ba.piece_at(move.to_square)
    if not pc: return False
    return sum(1 for sq in ba.attacks(move.to_square)
               if (t := ba.piece_at(sq)) and t.color != pc.color
               and t.piece_type in (chess.KNIGHT,chess.BISHOP,chess.ROOK,chess.QUEEN,chess.KING)) >= 2

def _detect_pin(board, move):
    ba = board.copy(); ba.push(move); enemy = ba.turn
    for sq in chess.SQUARES:
        pc = ba.piece_at(sq)
        if pc and pc.color == enemy and pc.piece_type != chess.KING:
            if ba.is_pinned(enemy, sq) and not board.is_pinned(enemy, sq): return True
    return False

def _detect_discovered_attack(board, move):
    our = board.turn; ba = board.copy(); ba.push(move)
    for sq in chess.SQUARES:
        pc = ba.piece_at(sq)
        if pc and pc.color == our and sq != move.to_square and pc.piece_type in (chess.BISHOP,chess.ROOK,chess.QUEEN):
            for asq in (ba.attacks(sq) & ~board.attacks(sq)):
                t = ba.piece_at(asq)
                if t and t.color != our and PIECE_VALUES.get(t.piece_type,0) >= 3: return True
    return False

def _detect_back_rank_mate(board, move):
    ba = board.copy(); ba.push(move)
    if not ba.is_checkmate(): return False
    ksq = ba.king(ba.turn)
    return ksq is not None and chess.square_rank(ksq) in (0,7)

def _detect_winning_capture(board, move):
    if not board.is_capture(move): return None
    cap = board.piece_at(move.to_square)
    if not cap: return "capture"
    mov = board.piece_at(move.from_square)
    if not mov: return None
    cv,mv2 = PIECE_VALUES.get(cap.piece_type,0), PIECE_VALUES.get(mov.piece_type,0)
    if cv > mv2: return "winning_capture"
    if cv == mv2: return "trade"
    return None

def detect_tactics(board, move):
    ba = board.copy(); ba.push(move)
    if ba.is_checkmate():
        return ["back_rank_mate"] if _detect_back_rank_mate(board, move) else ["checkmate"]
    t = []
    if ba.is_check(): t.append("check")
    if _detect_fork(board, move): t.append("fork")
    if _detect_pin(board, move): t.append("pin")
    if _detect_discovered_attack(board, move): t.append("discovered_attack")
    c = _detect_winning_capture(board, move)
    if c: t.append(c)
    return t


# ═══ ANALYSIS FUNCTIONS ══════════════════════════════════════════════════════

def run_stockfish_analysis(fen, depth=18, multipv=3):
    try:
        board = chess.Board(fen); engine = get_engine()
        is_white = board.turn == chess.WHITE
        info_list = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
        top_moves = []
        for i, info in enumerate(info_list):
            mv = info["pv"][0]
            rel_cp = info["score"].relative.score(mate_score=10_000)
            abs_cp = _relative_to_absolute(rel_cp, is_white)
            pv_san = []; tmp = board.copy()
            for pm in info["pv"][:5]:
                if pm in tmp.legal_moves: pv_san.append(tmp.san(pm)); tmp.push(pm)
            top_moves.append({
                "rank": i+1, "move_san": board.san(mv), "move_uci": str(mv),
                "score_cp": abs_cp, "score_pawns": _format_pawns(abs_cp),
                "eval_label": _eval_description(abs_cp),
                "mate_in": info["score"].relative.mate(), "pv": pv_san,
                "tactics": detect_tactics(board, mv),
            })
        ecp = top_moves[0]["score_cp"] if top_moves else 0
        return {"fen":fen,"turn":"White" if is_white else "Black",
                "eval_cp":ecp,"eval_pawns":_format_pawns(ecp),
                "eval_label":_eval_description(ecp),"top_moves":top_moves}
    except Exception as e:
        return {"error": str(e)}

def evaluate_user_move(fen, move_uci, depth=18):
    try:
        board = chess.Board(fen); move = chess.Move.from_uci(move_uci)
        is_white = board.turn == chess.WHITE; engine = get_engine()
        top = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=1)
        best_abs = _relative_to_absolute(
            top[0]["score"].relative.score(mate_score=10_000) or 0, is_white)
        san = board.san(move); board.push(move)
        after = engine.analyse(board, chess.engine.Limit(depth=depth), multipv=1)
        after_abs = _relative_to_absolute(
            after[0]["score"].relative.score(mate_score=10_000) or 0, not is_white)
        board.pop()
        cp_loss = (best_abs - after_abs) if is_white else (after_abs - best_abs)
        cp_loss = max(0, int(cp_loss))
        return {"move_san":san,"move_uci":move_uci,
                "score_cp":after_abs,"score_pawns":_format_pawns(after_abs),
                "cp_loss":cp_loss,"loss_pawns":round(cp_loss/100,1),
                "label":_cp_label(cp_loss),"eval_label":_eval_description(after_abs),
                "tactics":detect_tactics(board, move)}
    except Exception as e:
        return {"error": str(e)}


# ═══ @TOOL WRAPPERS ═════════════════════════════════════════════════════════

@tool
def analyze_position_tool(fen: str, depth: int = 18) -> str:
    """Run Stockfish analysis. Returns top 3 moves with absolute pawn evals and detected tactics."""
    return json.dumps(run_stockfish_analysis(fen, depth=depth))

@tool
def evaluate_move_tool(fen: str, move_uci: str, depth: int = 18) -> str:
    """Grade a specific move WITHOUT playing it. Returns eval in pawn units, loss from best, quality label, tactics."""
    return json.dumps(evaluate_user_move(fen, move_uci, depth=depth))

@tool
def get_legal_moves_tool(fen: str) -> str:
    """List all legal moves in a position (SAN format)."""
    try:
        board = chess.Board(fen)
        return json.dumps({"legal_moves":[board.san(m) for m in board.legal_moves],
                          "count":len(list(board.legal_moves)),
                          "turn":"White" if board.turn==chess.WHITE else "Black"})
    except Exception as e:
        return json.dumps({"error":str(e)})

@tool
def get_position_info_tool(fen: str) -> str:
    """Get position details: turn, material, check/mate/stalemate status."""
    try:
        board = chess.Board(fen)
        mat = {}
        for cn,c in [("white",chess.WHITE),("black",chess.BLACK)]:
            mat[cn] = {n:len(board.pieces(pt,c)) for pt,n in
                       [(chess.PAWN,"pawns"),(chess.KNIGHT,"knights"),(chess.BISHOP,"bishops"),
                        (chess.ROOK,"rooks"),(chess.QUEEN,"queens")]}
        return json.dumps({"fen":fen,"turn":"White" if board.turn==chess.WHITE else "Black",
                          "fullmove_number":board.fullmove_number,"is_check":board.is_check(),
                          "is_checkmate":board.is_checkmate(),"is_stalemate":board.is_stalemate(),
                          "is_game_over":board.is_game_over(),"material":mat})
    except Exception as e:
        return json.dumps({"error":str(e)})

@tool
def normalize_move_tool(move: str, fen: str) -> str:
    """Translate a chess move (SAN/UCI) to standardized UCI for the given position."""
    return json.dumps(normalize_move(move, fen))

@tool
def search_openings_tool(query: str) -> str:
    """
    Search the chess openings knowledge base using semantic search (RAG).

    Use this tool in Analysis Mode when:
    - The user asks about a specific opening ("what is the Sicilian?",
      "tell me about the Catalan")
    - The user asks for opening recommendations ("what should I play
      against 1.e4?", "aggressive openings for White")
    - The user wants to know the plans, key ideas, or common mistakes
      in the current opening ("what's the plan here?", "what should
      White be doing?")
    - You need to identify what opening the current position belongs to

    The knowledge base contains 3,650 enriched opening entries with:
    name, ECO code, moves, summary, plans for White, plans for Black,
    key squares, and common mistakes.

    Parameters
    ----------
    query : natural language query about chess openings

    Returns a formatted string with up to 5 matching openings and their
    full details. Present the most relevant result(s) to the user.
    """
    if not _RAG_AVAILABLE:
        return "Opening knowledge base not available. Run: python openings_rag.py --build --jsonl openings_enriched.jsonl"
    results = search_openings(query, k=5)
    return format_results_for_agent(results)

ALL_TOOLS = [analyze_position_tool, evaluate_move_tool, get_legal_moves_tool,
             get_position_info_tool, normalize_move_tool, search_openings_tool]


# ═══ PLAIN FUNCTIONS ═════════════════════════════════════════════════════════

def normalize_move(move_str, fen):
    try: board = chess.Board(fen)
    except: return {"uci":"INVALID","san":move_str,"is_legal":False}
    raw = (move_str or "").strip()
    candidates = [raw]
    tokens = raw.split()
    if tokens and tokens[0] != raw: candidates.append(tokens[0])
    for c in list(candidates):
        for ch in ("+","#","!","?"): 
            if ch in c: candidates.append(c.replace(ch,""))
    for cand in candidates:
        if not cand: continue
        try:
            mv = board.parse_san(cand)
            return {"uci":mv.uci(),"san":board.san(mv),"is_legal":True}
        except: pass
        try:
            mv = chess.Move.from_uci(cand)
            if mv in board.legal_moves: return {"uci":mv.uci(),"san":board.san(mv),"is_legal":True}
        except: pass
    return {"uci":"INVALID","san":raw,"is_legal":False}


# Move-like pattern: anything that resembles chess notation
_MOVE_LIKE = re.compile(
    r"^\s*(?:"
    r"O-O(?:-O)?"                                          # castling
    r"|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?" # SAN
    r"|[a-h][1-8][a-h][1-8][qrbn]?"                         # UCI
    r")[!?+#]*\s*$"
)

_CONVERSATION_KEYWORDS = {
    "hint","help","stuck","clue","clues","tip","tips","advice",
    "thanks","thank","ty","thx","ok","okay","cool","nice","good","great",
    "wow","yeah","yes","no","sure","yep","yup","alright",
    "another","next","more","again","skip","new","puzzle","puzzles","give","load",
    "what","why","how","where","who","explain","show","tell","about","would","if",
    "analyze","analysis","evaluate","eval","undo","back",
    "done","quit","stop","leave","wait","hmm","hmmm",
    "can","you","the","my","this","that","is","are","do","does",
    "please","me","it","best","top","position","assessment",
}


def classify_input(user_text, fen):
    """
    Classify user input as MOVE, ILLEGAL_MOVE, or CONVERSATION.

    Simplified from 4 types to 3: UNREADABLE was merged into ILLEGAL_MOVE.
    Anything that looks like chess notation but doesn't parse as a legal
    move returns ILLEGAL_MOVE with a "not legal" message. This eliminates
    the confusing "couldn't read" error for inputs like "d4" that are
    valid notation but not legal in the current position.
    """
    raw = (user_text or "").strip()

    # 1. Try parsing as a legal move — the golden path
    n = normalize_move(raw, fen)
    if n["is_legal"]:
        return {"type":"MOVE","uci":n["uci"],"san":n["san"]}

    # 2. Single short string (1-5 chars) that matches the move pattern?
    #    It's almost certainly a move attempt, not conversation.
    #    Return ILLEGAL_MOVE so the user gets "not legal here" rather
    #    than "couldn't read that."
    stripped = raw.strip()
    if len(stripped) <= 5 and _MOVE_LIKE.match(stripped):
        return {"type":"ILLEGAL_MOVE","uci":"INVALID","san":raw}

    # 3. Multi-word input with any conversation keyword → conversation
    lw = re.findall(r"[a-zA-Z']+", raw.lower())
    if len(lw) >= 2 and any(w in _CONVERSATION_KEYWORDS for w in lw):
        return {"type":"CONVERSATION","uci":"INVALID","san":raw}

    # 4. Single word that IS a conversation keyword → conversation
    if len(lw) == 1 and lw[0] in _CONVERSATION_KEYWORDS:
        return {"type":"CONVERSATION","uci":"INVALID","san":raw}

    # 5. Anything else that matches the move pattern → ILLEGAL_MOVE
    if _MOVE_LIKE.match(stripped):
        return {"type":"ILLEGAL_MOVE","uci":"INVALID","san":raw}

    # 6. Default → conversation
    return {"type":"CONVERSATION","uci":"INVALID","san":raw}
