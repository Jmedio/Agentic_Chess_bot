# app.py — Legal Danish: Chess Puzzle & Analysis Mode

import re, io, random
import streamlit as st
import chess, chess.pgn
from chess_bot_main_1 import initialize_messages, get_response
from puzzle_db import get_random_puzzle, get_puzzle_counts, mark_puzzle_solved
from chess_tools import classify_input, normalize_move
from board_render import (
    render_board_svg, push_moves, uci_to_san, fen_turn_color,
    ARROW_COLOR_BLUE, ARROW_COLOR_RED,
)
from spinner_phrases import pick_spinner


main_image = "files/images/chess_board_image.jpeg"
bot_icon   = "files/images/bot_picture.jpeg"
user_icon  = "files/images/user_image.jpg"

st.set_page_config(page_title="Chess Eval Bot", layout="centered")

# Puzzle items
STATUS_TAGS = (
    "STATUS: CORRECT","STATUS: INCORRECT","STATUS: SOLVED",
    "STATUS: HINT","STATUS: NEXT_PUZZLE",
)
def _strip_status(t):
    for tag in STATUS_TAGS: t = t.replace(tag,"")
    return t.strip()

CELEBRATION_HEADERS = [
    "🎉 Brilliant solve!","✨ Beautifully played!","🏆 Puzzle conquered!",
    "🔥 Stunning work!","♟️ Checkmate the puzzle!","🌟 Magnificent!",
    "⚡ A masterful strike!","🎯 Right on target!",
]
CELEBRATION_GRADIENTS = [
    ("#d4edda","#c3e6cb","#155724"),("#d1ecf1","#bee5eb","#0c5460"),
    ("#fff3cd","#ffeeba","#856404"),("#e2d5f0","#cdb4db","#5a3a78"),
]
def _celebration_html(text):
    bg1,bg2,fg = random.choice(CELEBRATION_GRADIENTS)
    h = random.choice(CELEBRATION_HEADERS)
    return (f'<div style="background:linear-gradient(135deg,{bg1},{bg2});'
            f'padding:14px 18px;border-radius:10px;border-left:5px solid {fg};margin:4px 0">'
            f'<div style="font-size:17px;font-weight:700;color:{fg};margin-bottom:6px">{h}</div>'
            f'<div style="color:{fg};font-size:15px;line-height:1.5">{text}</div></div>')


#SAN notes
SAN_CHEAT_SHEET = """
**Piece Letters**
| Letter | Piece |
|--------|-------|
| K | King |
| Q | Queen |
| R | Rook |
| B | Bishop |
| N | Knight |
| *(none)* | Pawn |

**Move Examples**
| Notation | Meaning |
|----------|---------|
| `e4` | Pawn to e4 |
| `Nf3` | Knight to f3 |
| `Bb5` | Bishop to b5 |
| `O-O` | Kingside castle |
| `O-O-O` | Queenside castle |

**Special Moves**
| Notation | Meaning |
|----------|---------|
| `exd5` | Pawn on e-file captures on d5 |
| `Nxe5` | Knight captures on e5 |
| `Qh5+` | Queen to h5, giving check |
| `Qf7#` | Queen to f7, checkmate |
| `e8=Q` | Pawn promotes to queen |
| `R1e1` | Rook from rank 1 to e1 (disambiguating) |
"""

#Hypothetical
_HYPOTHETICAL_PATTERNS = [
    r"what\s+(?:about|if|does|would|happens)\b.*?([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)",
    r"how\s+(?:is|about|good|bad)\b.*?([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)",
    r"(?:is|would)\s+([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)\s+(?:good|bad|ok|best|better|worse|playable|safe|risky|sound|viable|strong|weak|work|effective)",
    r"(?:should\s+I|can\s+I|could\s+I)\s+(?:play\s+)?([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)",
    r"(?:evaluate|eval|analyze|check)\s+([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)",
]

def _extract_hypothetical_move(user_text, fen):
    for pattern in _HYPOTHETICAL_PATTERNS:
        m = re.search(pattern, user_text, re.IGNORECASE)
        if m:
            norm = normalize_move(m.group(1), fen)
            if norm["is_legal"]:
                return norm
    return None


#Move history + PGN string
def _move_history_to_pgn(starting_fen, uci_moves):
    """
    Convert the UCI move history to a readable PGN string.
    e.g. ["e2e4","e7e5","g1f3","b8c6","d2d4"] → "1. e4 e5 2. Nf3 Nc6 3. d4"

    This is critical for opening identification — the RAG search matches
    against move sequences in the openings database.
    """
    try:
        board = chess.Board(starting_fen)
    except Exception:
        board = chess.Board()

    parts = []
    for uci in uci_moves:
        try:
            mv = chess.Move.from_uci(uci)
            if mv not in board.legal_moves:
                break
            if board.turn == chess.WHITE:
                parts.append(f"{board.fullmove_number}. {board.san(mv)}")
            else:
                parts.append(board.san(mv))
            board.push(mv)
        except Exception:
            break

    return " ".join(parts)


#MSG Help
def _render_message(msg):
    if msg["role"] == "user":
        st.chat_message("user", avatar=user_icon).write(
            msg.get("display", msg["content"]))
    elif msg["role"] == "assistant":
        cl = _strip_status(msg["content"])
        if msg.get("celebrated"):
            with st.chat_message("assistant", avatar=bot_icon):
                st.markdown(_celebration_html(cl), unsafe_allow_html=True)
        else:
            st.chat_message("assistant", avatar=bot_icon).write(cl)


# Redos

def _load_new_puzzle():
    dk = st.session_state.get("difficulty_key","intermediate")
    p = get_random_puzzle(difficulty=dk, seen_ids=st.session_state.get("seen_puzzle_ids",set()))
    if not p:
        st.session_state.puzzle_error = f"No unseen puzzles at **{dk.capitalize()}**."
        return
    sol = p["solution_moves"]; su = sol[0] if sol else None
    sf = push_moves(p["fen"],[su]) if su else p["fen"]
    st.session_state.update({
        "active_puzzle":p,"puzzle_move_idx":1,"current_fen":sf,
        "setup_move_uci":su,"last_move_uci":None,
        "user_color":fen_turn_color(sf),"puzzle_error":None,
        "messages":initialize_messages(),"puzzle_completed":False,
        "last_solved_themes":"","final_move_uci":None,
    })
    seen = st.session_state.get("seen_puzzle_ids",set()); seen.add(p["puzzle_id"])
    st.session_state.seen_puzzle_ids = seen

def _skip_puzzle(): _load_new_puzzle()

def _load_analysis_position():
    raw = st.session_state.get("analysis_input_text","").strip()
    if not raw:
        st.session_state.analysis_error = "Please enter a FEN or PGN."; return
    loaded = None
    # Try FEN
    try: loaded = chess.Board(raw).fen()
    except: pass
    # Try PGN — also save the initial PGN text for move history seeding
    initial_pgn_moves = []
    if not loaded:
        try:
            g = chess.pgn.read_game(io.StringIO(raw))
            if g:
                loaded = g.end().board().fen()
                # Extract the moves as UCI for the history
                board = g.board()
                node = g
                while node.variations:
                    nxt = node.variations[0]
                    initial_pgn_moves.append(nxt.move.uci())
                    board.push(nxt.move)
                    node = nxt
        except: pass
    if not loaded:
        st.session_state.analysis_error = "Could not parse as FEN or PGN."; return
    uc = fen_turn_color(loaded)
    st.session_state.update({
        "analysis_fen":loaded,"analysis_starting_fen":chess.STARTING_FEN if initial_pgn_moves else loaded,
        "analysis_move_history":initial_pgn_moves,
        "analysis_last_move":initial_pgn_moves[-1] if initial_pgn_moves else None,
        "analysis_error":None,"current_fen":loaded,"last_move_uci":None,
        "user_color":uc,"analysis_flipped":(uc=="black"),
        "messages":initialize_messages(),
    })

def _undo_analysis_move():
    h = st.session_state.get("analysis_move_history",[])
    if not h: return
    h.pop()
    sf = st.session_state.get("analysis_starting_fen",chess.STARTING_FEN)
    nf = push_moves(sf,h)
    st.session_state.update({
        "analysis_move_history":h,"analysis_fen":nf,"current_fen":nf,
        "last_move_uci":h[-1] if h else None,"analysis_last_move":h[-1] if h else None,
    })

def _restart_chat():
    for k in list(st.session_state.keys()):
        if k != "current_mode": del st.session_state[k]


#Context Builder

def _build_analysis_context(
    fen, input_type,
    move_info=None, previous_fen=None,
    is_user_move=None, user_color=None,
    analysis_requested=False,
    hypothetical=None,
    move_history_pgn="",
):
    lines = [
        "[ANALYSIS CONTEXT]",
        f"CURRENT_MODE: ANALYSIS",
        f"fen: {fen}",
        f"turn: {fen_turn_color(fen).capitalize()}",
        f"user_input_type: {input_type}",
        f"analysis_requested: {str(analysis_requested).lower()}",
    ]
    if move_history_pgn:
        lines.append(f"move_history_pgn: {move_history_pgn}")
    if user_color:
        lines.append(f"user_color: {user_color}")
    if move_info:
        lines.append(f"move_san: {move_info.get('san','')}")
        lines.append(f"move_uci: {move_info.get('uci','')}")
    if is_user_move is not None:
        lines.append(f"is_user_move: {str(is_user_move).lower()}")
    if previous_fen:
        lines.append(f"previous_fen: {previous_fen}")
    if hypothetical:
        lines.append(f"hypothetical_move_uci: {hypothetical['uci']}")
        lines.append(f"hypothetical_move_san: {hypothetical['san']}")
    lines.append("[END ANALYSIS CONTEXT]")
    return "\n".join(lines)


#Session State Default

_D = {
    "messages":initialize_messages(),"current_fen":None,"last_move_uci":None,
    "user_color":None,"current_mode":"Chess Puzzle",
    "active_puzzle":None,"puzzle_move_idx":0,"setup_move_uci":None,
    "seen_puzzle_ids":set(),"puzzle_error":None,"difficulty_key":"intermediate",
    "puzzle_completed":False,"last_solved_themes":"","final_move_uci":None,
    "analysis_fen":None,"analysis_starting_fen":None,"analysis_move_history":[],
    "analysis_last_move":None,"analysis_error":None,"analysis_input_text":"",
    "analysis_flipped":False,
}
for k,v in _D.items():
    if k not in st.session_state: st.session_state[k] = v


#Sidebar

with st.sidebar:
    st.title("Legal Danish ♟️")

    with st.expander("♟ Notation Cheat Sheet"):
        st.markdown(SAN_CHEAT_SHEET)

    st.divider()

    st.subheader("Mode")
    sel = st.selectbox("Mode",["Chess Puzzle","Analysis Mode"],label_visibility="collapsed")
    st.session_state.current_mode = sel
    st.divider()

    if sel == "Chess Puzzle":
        st.subheader("Puzzle Difficulty")
        d = st.select_slider("Difficulty",["Beginner","Intermediate","Advanced"],
                             value="Intermediate",label_visibility="collapsed")
        st.session_state.difficulty_key = d.lower()
        c = get_puzzle_counts()
        if sum(c.values()) > 0:
            st.caption(f"Beginner {c.get('beginner',0):,} · Intermediate {c.get('intermediate',0):,} · Advanced {c.get('advanced',0):,}")
        else:
            st.warning("No puzzles in DB.")
        st.divider()
        st.button("🧩 Give me a puzzle",on_click=_load_new_puzzle,use_container_width=True)
        if st.session_state.puzzle_error: st.error(st.session_state.puzzle_error)
        if st.session_state.active_puzzle:
            p = st.session_state.active_puzzle; st.divider(); st.caption("Current puzzle")
            st.write(f"**Difficulty:** {p['difficulty'].capitalize()}")
            st.write(f"**Rating:** {p['rating']}")
            if st.session_state.user_color:
                st.write(f"**You're playing:** {st.session_state.user_color.capitalize()}")
            st.button("Skip this puzzle",on_click=_skip_puzzle,use_container_width=True)
        if st.session_state.puzzle_completed and not st.session_state.active_puzzle and st.session_state.last_solved_themes:
            st.divider(); st.caption("Last solved puzzle's themes")
            st.markdown(" ".join(
                f'<span style="display:inline-block;background:#e9ecef;color:#495057;'
                f'padding:2px 8px;margin:2px;border-radius:10px;font-size:12px">{t}</span>'
                for t in st.session_state.last_solved_themes.split()), unsafe_allow_html=True)

    elif sel == "Analysis Mode":
        st.subheader("Load Position")
        st.text_area(
            "FEN or PGN (1. e4 e5 2. Nf3 Nc6)",
            key="analysis_input_text", height=100,
            placeholder=" 1. e4 e5 2. Nf3 Nc6\n\n"
                        "rnbqkbnr/pp...,"
        )
        st.button("📋 Load Position",on_click=_load_analysis_position,use_container_width=True)
        if st.session_state.analysis_error: st.error(st.session_state.analysis_error)
        if st.session_state.analysis_fen:
            st.divider(); st.caption("Position")
            turn = fen_turn_color(st.session_state.analysis_fen).capitalize()
            nm = len(st.session_state.get("analysis_move_history",[]))
            st.write(f"**Turn:** {turn}")
            st.write(f"**Moves played:** {nm}")
            if st.session_state.user_color:
                st.write(f"**You started as:** {st.session_state.user_color.capitalize()}")
            if nm > 0:
                st.button("↩️ Undo last move",on_click=_undo_analysis_move,use_container_width=True)

    st.divider()
    st.button("Restart Game Chat",on_click=_restart_chat)


#Header

c1,c2 = st.columns([1,5])
with c1: st.image(main_image,width=80)
with c2: st.title("Legal Danish: The chess evaluation bot"); st.caption("Just some chess help")


#Board Panel

if st.session_state.current_fen:
    mode = st.session_state.current_mode
    au = None; ac = ARROW_COLOR_BLUE; fv = None

    if mode == "Chess Puzzle":
        if st.session_state.active_puzzle and st.session_state.puzzle_move_idx == 1 and st.session_state.setup_move_uci:
            au = st.session_state.setup_move_uci; ac = ARROW_COLOR_BLUE
        elif st.session_state.puzzle_completed and st.session_state.final_move_uci:
            au = st.session_state.final_move_uci; ac = ARROW_COLOR_RED
        if st.session_state.puzzle_completed and st.session_state.user_color:
            fv = (st.session_state.user_color == "black")
    elif mode == "Analysis Mode":
        fv = st.session_state.get("analysis_flipped", False)

    svg = render_board_svg(
        fen=st.session_state.current_fen,
        last_move_uci=st.session_state.last_move_uci,
        arrow_uci=au, arrow_color=ac, size=380, flipped=fv,
    )
    st.markdown(
        f'<div style="display:flex;justify-content:center;padding:8px 0 14px 0">{svg}</div>',
        unsafe_allow_html=True)

    if mode == "Chess Puzzle":
        if st.session_state.active_puzzle and st.session_state.puzzle_move_idx == 1 and st.session_state.setup_move_uci:
            st.caption(f"The blue arrow shows your opponent's last move — find the best reply for **{(st.session_state.user_color or 'the side').capitalize()}**.")
        elif st.session_state.puzzle_completed and st.session_state.final_move_uci:
            st.caption("The red arrow marks your final winning move.")
    elif mode == "Analysis Mode":
        turn = fen_turn_color(st.session_state.current_fen).capitalize()
        st.caption(f"**{turn}** to move.")


#Dropdown bar

def _find_split_index(msgs, keep_exchanges=2):
    """
    Find the index at which to split messages into 'collapsed' and 'visible.'
    Splits on exchange boundaries so a bot response never gets orphaned
    into the collapsed section without its user message.

    keep_exchanges=2 means the last 2 complete user→assistant exchanges
    stay visible. Everything before that goes into the expander.
    """
    # Find all user message indices (each marks the start of an exchange)
    user_idxs = [i for i, m in enumerate(msgs) if m["role"] == "user"]
    if len(user_idxs) <= keep_exchanges:
        return None  # not enough exchanges to collapse anything
    # Split just before the (keep_exchanges)-th-from-last user message
    return user_idxs[-keep_exchanges]


def _render_collapsed_message(msg):
    """Render a message in compact form for inside the expander.
    Uses styled HTML instead of st.chat_message to avoid expander styling bugs."""
    if msg["role"] == "user":
        text = msg.get("display", msg["content"])
        st.markdown(
            f'<div style="background:#2b313e;padding:8px 12px;border-radius:8px;'
            f'margin:4px 0;font-size:14px;color:#c9d1d9">'
            f'<strong style="color:#58a6ff">You:</strong> {text}</div>',
            unsafe_allow_html=True)
    elif msg["role"] == "assistant":
        text = _strip_status(msg["content"])
        # Truncate long messages in the collapsed view
        if len(text) > 200:
            text = text[:200] + "..."
        if msg.get("celebrated"):
            text = "🎉 " + text
        st.markdown(
            f'<div style="background:#1c1f26;padding:8px 12px;border-radius:8px;'
            f'margin:4px 0;font-size:14px;color:#a0a8b4">'
            f'<strong style="color:#7ee787">Legal Danish:</strong> {text}</div>',
            unsafe_allow_html=True)


all_msgs = st.session_state.messages
split_at = _find_split_index(all_msgs, keep_exchanges=2)

if split_at is not None:
    collapsed = all_msgs[:split_at]
    visible   = all_msgs[split_at:]

    with st.expander(f"Earlier messages ({len(collapsed)})"):
        for msg in collapsed:
            _render_collapsed_message(msg)

    for msg in visible:
        _render_message(msg)
else:
    # Not enough history to collapse — render everything normally
    for msg in all_msgs:
        _render_message(msg)


#Chat input

_PH = {
    "Chess Puzzle":  "Type your move, e.g. Nf3 or e4...",
    "Analysis Mode": "Play a move or ask to evaluate the position...",
}
user_input = st.chat_input(_PH.get(st.session_state.current_mode,"Type here..."))

if user_input:
    mode = st.session_state.current_mode

    #Puzzle Mode
    if mode == "Chess Puzzle":
        if st.session_state.active_puzzle is None:
            aff = {"yes","yeah","yep","yup","sure","ok","okay","another","next","more","again","go","ready","puzzle","please"}
            dec = {"no","nope","stop","done","quit","leave"}
            ws = set(user_input.lower().replace(",","").replace(".","").split())
            if (ws & aff) and not (ws & dec): _load_new_puzzle(); st.rerun()

        st.chat_message("user",avatar=user_icon).write(user_input)
        pz = st.session_state.active_puzzle; mi = st.session_state.puzzle_move_idx
        fn = st.session_state.current_fen
        if pz and fn:
            cl = classify_input(user_input,fn); sol = pz["solution_moves"]
            eu = sol[mi] if mi < len(sol) else ""
            es = uci_to_san(fn,eu) if eu else ""
            isf = (mi+1) >= len(sol)
            ctx = ["[PUZZLE CONTEXT]",f"CURRENT_MODE: PUZZLE",f"puzzle_id: {pz['puzzle_id']}",f"fen: {fn}",
                   f"themes: {pz.get('themes','')}",f"move_index: {mi}",
                   f"expected_move_uci: {eu}",f"expected_move_san: {es}",
                   f"user_input_type: {cl['type']}"]
            if cl["type"] == "MOVE":
                ctx.append(f"user_move_uci: {cl['uci']}"); ctx.append(f"user_move_san: {cl['san']}")
            ctx.append(f"is_final_move: {str(isf).lower()}"); ctx.append("[END PUZZLE CONTEXT]")
            ai = user_input+"\n\n"+"\n".join(ctx)
        else: ai = user_input; cl = {"type":None}
        with st.spinner(pick_spinner(mode,cl.get("type"))):
            resp,upd = get_response(st.session_state.messages,ai)
        for m in reversed(upd):
            if m["role"]=="user" and m["content"]==ai: m["display"]=user_input; break
        ic = "STATUS: CORRECT" in resp; isl = "STATUS: SOLVED" in resp; inp = "STATUS: NEXT_PUZZLE" in resp
        bc = False
        if pz and fn and cl.get("type")=="MOVE" and (ic or isl):
            mvs = [cl["uci"]]; o = mi+1
            if o < len(pz["solution_moves"]): mvs.append(pz["solution_moves"][o])
            st.session_state.current_fen = push_moves(fn,mvs)
            st.session_state.last_move_uci = mvs[-1]; bc = True
            if mi==1: st.session_state.setup_move_uci = None
        if pz and cl.get("type")=="MOVE" and (ic or isl):
            if isl:
                mark_puzzle_solved(pz["puzzle_id"])
                st.session_state.update({"last_solved_themes":pz.get("themes",""),
                    "puzzle_completed":True,"final_move_uci":cl["uci"],
                    "active_puzzle":None,"puzzle_move_idx":0,"setup_move_uci":None})
                if upd and upd[-1]["role"]=="assistant": upd[-1]["celebrated"]=True
            else:
                ni = mi+2
                if ni >= len(pz["solution_moves"]):
                    mark_puzzle_solved(pz["puzzle_id"])
                    st.session_state.update({"last_solved_themes":pz.get("themes",""),
                        "puzzle_completed":True,"final_move_uci":cl["uci"],
                        "active_puzzle":None,"puzzle_move_idx":0,"setup_move_uci":None})
                else: st.session_state.puzzle_move_idx = ni
        st.session_state.messages = upd; cln = _strip_status(resp)
        if bc: st.rerun()
        if inp: _load_new_puzzle(); st.rerun()
        if isl:
            with st.chat_message("assistant",avatar=bot_icon):
                st.markdown(_celebration_html(cln),unsafe_allow_html=True)
        else: st.chat_message("assistant",avatar=bot_icon).write(cln)

    #Analysis mode
    elif mode == "Analysis Mode":
        st.chat_message("user",avatar=user_icon).write(user_input)
        fn = st.session_state.analysis_fen
        uc = st.session_state.user_color

        if not fn:
            ai = user_input; cl = {"type":"CONVERSATION"}
        else:
            cl = classify_input(user_input,fn)

        # Build the PGN move history for opening identification
        pgn_history = _move_history_to_pgn(
            st.session_state.get("analysis_starting_fen", chess.STARTING_FEN),
            st.session_state.get("analysis_move_history", []),
        )

        is_undo = False
        analysis_requested = False
        hypothetical = None

        if cl["type"] == "CONVERSATION":
            if any(w in user_input.lower().split() for w in ("undo","back","takeback")):
                is_undo = True
            else:
                hypothetical = _extract_hypothetical_move(user_input, fn) if fn else None
                if hypothetical is None:
                    req_words = {"evaluate","eval","analyze","analysis","engine",
                                "stockfish","best","top","position","assessment"}
                    if any(w in user_input.lower().split() for w in req_words):
                        analysis_requested = True

        bc = False

        if is_undo:
            _undo_analysis_move()
            fn = st.session_state.analysis_fen
            pgn_history = _move_history_to_pgn(
                st.session_state.get("analysis_starting_fen", chess.STARTING_FEN),
                st.session_state.get("analysis_move_history", []),
            )
            ctx = _build_analysis_context(fn or "", "CONVERSATION",
                                          user_color=uc, move_history_pgn=pgn_history)
            ai = user_input + "\n\n" + ctx
            bc = True

        elif fn and cl["type"] == "MOVE":
            mu = cl["uci"]; pre = fn
            turn_before = fen_turn_color(pre)
            is_um = (turn_before == uc) if uc else True
            nf = push_moves(pre,[mu])
            h = st.session_state.get("analysis_move_history",[])
            h.append(mu)
            st.session_state.update({
                "analysis_move_history":h,"analysis_fen":nf,
                "analysis_last_move":mu,"current_fen":nf,"last_move_uci":mu,
            })
            # Rebuild PGN with the new move included
            pgn_history = _move_history_to_pgn(
                st.session_state.get("analysis_starting_fen", chess.STARTING_FEN), h,
            )
            ctx = _build_analysis_context(
                nf, "MOVE",
                move_info={"san":cl["san"],"uci":mu},
                previous_fen=pre, is_user_move=is_um, user_color=uc,
                move_history_pgn=pgn_history,
            )
            ai = user_input + "\n\n" + ctx
            bc = True

        elif fn and cl["type"] == "ILLEGAL_MOVE":
            ctx = _build_analysis_context(fn,"ILLEGAL_MOVE",user_color=uc,
                                          move_history_pgn=pgn_history)
            ai = user_input + "\n\n" + ctx

        elif fn and cl["type"] == "CONVERSATION":
            ctx = _build_analysis_context(
                fn, "CONVERSATION",
                user_color=uc, analysis_requested=analysis_requested,
                hypothetical=hypothetical, move_history_pgn=pgn_history,
            )
            ai = user_input + "\n\n" + ctx
        else:
            ai = user_input

        with st.spinner(pick_spinner(mode,cl.get("type"))):
            resp,upd = get_response(st.session_state.messages,ai)

        for m in reversed(upd):
            if m["role"]=="user" and m["content"]==ai: m["display"]=user_input; break

        st.session_state.messages = upd
        cln = _strip_status(resp)

        if bc: st.rerun()
        st.chat_message("assistant",avatar=bot_icon).write(cln)