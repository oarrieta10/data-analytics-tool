"""synth_form.py — the synthetic profile as a form, drawn from the config's field metadata.

Every setting in synth/config.py carries a section, label, help, unit and
range (knob()); this module turns those into widgets, so a new setting shows
up without UI code.  Widget values live in st.session_state under
"syn::<section>::<field>" keys; load_into_form() fills them from a Profile and
form_to_profile() reads them back.  Shares are shown as percent.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re

import streamlit as st

from gtpfit.synth.config import DefectRates, PeakShape, Profile, load_profile

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_PROMO_LABEL = {"black_friday": "Black Friday", "cyber_monday": "Cyber Monday", "mid_july_sale": "Mid-July sale"}
_COMMON_CLOSURES = ["01-01", "07-04", "11-11", "12-24", "12-25", "12-26", "12-31"]
_MMDD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _fields():
    """(owner, field) for every setting: owner is None, 'defects' or 'peak_shape'."""
    for f in dataclasses.fields(Profile):
        if f.name not in ("defects", "peak_shape"):
            yield None, f
    for owner, cls in (("defects", DefectRates), ("peak_shape", PeakShape)):
        for f in dataclasses.fields(cls):
            yield owner, f


def key(f, sub=None) -> str:
    k = f"syn::{f.metadata['section']}::{f.name}"
    return k if sub is None else f"{k}::{sub}"


def _is_share(f):
    return f.metadata.get("unit") == "share"


# ---- profile <-> session state -------------------------------------------------

def load_into_form(p: Profile) -> None:
    ss = st.session_state
    for owner, f in _fields():
        v = getattr(getattr(p, owner) if owner else p, f.name)
        if f.name == "start_date":
            ss[key(f)] = dt.date.fromisoformat(v)
        elif f.name == "closed_dates":
            ss[key(f)] = list(v)
        elif f.name == "lead_time_days":
            ss[key(f)] = (int(v[0]), int(v[1]))
        elif isinstance(v, dict):
            for k, x in v.items():
                ss[key(f, k)] = type(f.metadata["lo"])(x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                ss[key(f, i)] = float(x)
        elif _is_share(f):
            ss[key(f)] = round(float(v) * 100, 2)
        else:
            ss[key(f)] = v


def form_to_profile() -> Profile:
    """Read the widgets back into a Profile (raises ValueError on a bad value)."""
    ss = st.session_state
    base = Profile()
    data, sections = {}, {"defects": {}, "peak_shape": {}}
    for owner, f in _fields():
        default = getattr(getattr(base, owner) if owner else base, f.name)
        if f.name == "start_date":
            v = ss[key(f)].isoformat()
        elif f.name == "closed_dates":
            v = list(ss[key(f)])
            bad = [d for d in v if not _MMDD.match(d)]
            if bad:
                raise ValueError(f"Closed dates must be MM-DD, got {bad}")
        elif f.name == "lead_time_days":
            v = list(ss[key(f)])
        elif isinstance(default, dict):
            v = {k: ss[key(f, k)] for k in default}
        elif isinstance(default, list):
            v = [ss[key(f, i)] for i in range(len(default))]
        elif _is_share(f):
            v = round(ss[key(f)] / 100, 4)
        else:
            v = type(default)(ss[key(f)])
        (sections[owner] if owner else data)[f.name] = v
    return load_profile(None, **data, **sections)


# ---- widgets ---------------------------------------------------------------------

def _scalar(f, label=None):
    md = f.metadata
    label = label or md["label"]
    k, h = key(f), md["help"]
    if f.type in ("bool", bool):
        return st.toggle(label, key=k, help=h)
    if f.name == "start_date":
        return st.date_input(label, key=k, help=h)
    if f.type in ("str", str):
        return st.text_input(label, key=k, help=h)
    lo, hi, step = md.get("lo"), md.get("hi"), md.get("step")
    if _is_share(f):
        return st.slider(label + " (%)", float(lo * 100), float(hi * 100), step=0.5, key=k, help=h)
    if md.get("widget") == "number" or lo is None or hi is None:
        return st.number_input(label, min_value=lo, max_value=hi, step=step or 1, key=k, help=h)
    return st.slider(label + (" (x)" if md.get("unit") == "x" else ""), lo, hi,
                     step=step or (1 if isinstance(lo, int) else 0.01), key=k, help=h)


def _compound(f):
    md = f.metadata
    st.markdown(f"**{md['label']}**", help=md["help"])
    if f.name == "weekday_factors":
        cols = st.columns(7)
        for i, c in enumerate(cols):
            c.number_input(_WEEKDAYS[i], min_value=md["lo"], max_value=md["hi"], step=md["step"], key=key(f, i))
    elif f.name == "promo_days":
        for k in Profile().promo_days:
            st.slider(_PROMO_LABEL.get(k, k) + " (x)", md["lo"], md["hi"], step=md["step"], key=key(f, k))
    elif f.name == "cover_days":
        cols = st.columns(4)
        for c, k in zip(cols, Profile().cover_days):
            c.number_input(f"Class {k}", min_value=md["lo"], max_value=md["hi"], step=md["step"], key=key(f, k))
    elif f.name == "lead_time_days":
        st.slider("days", md["lo"], md["hi"], step=md["step"], key=key(f), label_visibility="collapsed")
    elif f.name == "closed_dates":
        cur = st.session_state.get(key(f), [])
        st.multiselect("MM-DD", sorted(set(_COMMON_CLOSURES) | set(cur)), key=key(f), accept_new_options=True,
                       label_visibility="collapsed", placeholder="Add a date as MM-DD")


def _new_seed(f):
    st.session_state[key(f)] = int(dt.datetime.now().timestamp() * 1000) % 2_000_000_000


def render_section(section: str) -> None:
    ss = st.session_state
    group = None
    for owner, f in _fields():
        md = f.metadata
        if md["section"] != section:
            continue
        g = md.get("group")
        if g and g != group:
            st.markdown(f"##### {g}")
            group = g
        if f.name == "seed":
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            with c1:
                _scalar(f)
            c2.button("New seed", icon=":material/casino:", on_click=_new_seed, args=(f,), key="syn::new_seed")
        elif md.get("pairs_with"):
            base_f = next(b for o, b in _fields() if o is None and b.name == md["pairs_with"])
            normal = ss.get(key(base_f))
            unit = "%" if _is_share(f) else ""
            c1, c2 = st.columns([1, 3], vertical_alignment="center")
            c1.caption(f"normal {normal:g}{unit}" if normal is not None else "")
            with c2:
                _scalar(f, label=f"{md['label']} on peak")
        elif f.type in ("list", "dict", list, dict):
            _compound(f)
        else:
            _scalar(f)
