"""Shared look and building blocks for every FORGE page ("the evidence collection" style)."""
import base64
import io
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import streamlit as st
from PIL import Image

import storage
from schemas import raster_bytes

STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@800;900&display=swap');

:root {
  --display: 'DM Serif Display', Georgia, serif;
  --serif: 'Source Serif 4', Georgia, 'Times New Roman', serif;
  --mono: 'IBM Plex Mono', ui-monospace, monospace;
  --logo: 'Inter', 'Arial Black', sans-serif;
  --shadow: 0 1px 0 rgba(255,255,255,.4) inset, 0 10px 30px -12px rgba(60, 35, 15, .35);
}
html, body, .stApp, [data-testid="stMarkdownContainer"], input, textarea, select { font-family: var(--serif) !important; }
.stApp {
  background:
    radial-gradient(circle at 20% 0%, var(--glow), transparent 40%),
    radial-gradient(circle at 1px 1px, var(--dot) 1px, transparent 0) 0 0 / 22px 22px,
    var(--bg);
}
.stApp, [data-testid="stMarkdownContainer"], [data-testid="stMetricValue"], [data-testid="stHeader"] button { color: var(--text) !important; }
[data-testid="stHeader"] { background: transparent; }
#MainMenu, footer, [data-testid="stAppDeployButton"], [data-testid="stDecoration"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] { display: none !important; }
.block-container { padding-top: 1.75rem; padding-bottom: 8rem !important; max-width: 1360px; }
h1, h2, h3 { font-family: var(--display) !important; font-weight: 400 !important; color: var(--text) !important; letter-spacing: -.01em; }
h1 { font-size: 3.2rem !important; line-height: 1.05 !important; }
code { font-family: var(--mono) !important; background: var(--raise) !important; color: var(--text) !important; }
hr { border-color: var(--line) !important; }

/* Header */
.fx-header { display: flex; align-items: center; gap: 1.5rem; padding-bottom: 1rem; margin-bottom: 2rem;
  border-bottom: 1px solid var(--line-strong); }
.fx-header .logo { font-family: var(--logo); font-weight: 900; font-size: 2.4rem; letter-spacing: -.03em; line-height: 1; }
.fx-header .sub { font-family: var(--mono); font-size: .8rem; letter-spacing: .38em; text-transform: uppercase;
  border-left: 1px solid var(--line-strong); padding-left: 1.5rem; }
.fx-header .right { margin-left: auto; }
.fx-stamp { display: inline-block; font-family: var(--serif); font-weight: 700; text-transform: uppercase; letter-spacing: .12em;
  color: var(--accent); border: 2px solid var(--accent); border-radius: 6px; padding: .2rem .8rem; font-size: 1.15rem;
  transform: rotate(-2deg); opacity: .92; white-space: nowrap; }
.fx-stamp.big { font-size: clamp(1.1rem, 1.7vw, 1.55rem); transform: rotate(-7deg); padding: .15rem 1rem; box-shadow: 0 0 0 1px var(--accent) inset; }
.fx-stamp.small { font-size: .78rem; padding: .1rem .5rem; border-width: 1.5px; }
@media (max-width: 640px) { .fx-header { flex-wrap: wrap; gap: .75rem; } .fx-header .sub { letter-spacing: .2em; } }

/* Labels */
.fx-label { font-family: var(--mono); font-size: .72rem; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; color: var(--text); }
.fx-label.muted { color: var(--muted); font-weight: 500; }
.fx-note { font-family: var(--mono); font-size: .72rem; color: var(--muted); letter-spacing: .03em; margin-top: .6rem; }
.fx-lede { color: var(--muted); font-size: 1.15rem; max-width: 640px; margin: -.25rem 0 1.75rem; }
.fx-section { display: flex; align-items: center; gap: .9rem; margin: 2.25rem 0 1rem; font-family: var(--mono);
  font-size: .74rem; font-weight: 600; text-transform: uppercase; letter-spacing: .22em; color: var(--text); }
.fx-section span.n { color: var(--accent); }
.fx-section::after { content: ''; flex: 1; height: 1px; background: var(--line-strong); }
[data-testid="stCaptionContainer"] { color: var(--muted) !important; }

/* Paper cards */
.fx-card, [data-testid="stMetric"], [data-testid="stForm"] { background: var(--panel); border: 1px solid var(--line-strong);
  border-radius: 10px; box-shadow: var(--shadow); }
[data-testid="stMetric"] { padding: 1rem 1.25rem; }
[data-testid="stMetricLabel"] p { font-family: var(--mono) !important; text-transform: uppercase; letter-spacing: .18em;
  font-size: .7rem !important; color: var(--muted) !important; }
[data-testid="stMetricValue"] { font-family: var(--display); font-size: 2.6rem; }
[data-testid="stForm"] { padding: 1.5rem; }

/* Buttons */
.stButton button, .stFormSubmitButton button { border-radius: 8px; border: 1.5px solid var(--line-strong);
  background: var(--panel); color: var(--text); font-family: var(--serif) !important; transition: all .15s ease; }
.stButton button p, .stFormSubmitButton button p { font-family: var(--serif) !important; font-size: 1rem; }
.stButton button:hover, .stFormSubmitButton button:hover { border-color: var(--accent); color: var(--accent); background: var(--panel); }
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: var(--accent) !important; color: var(--on-accent) !important; border-color: var(--accent) !important; }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { filter: brightness(1.08); }
button [data-testid="stMarkdownContainer"], button [data-testid="stMarkdownContainer"] * { color: inherit !important; }
button:disabled, button:disabled:hover { opacity: .4 !important; cursor: not-allowed !important; }
button:focus:not(:active) { box-shadow: 0 0 0 2px var(--accent) !important; }

/* Inputs */
[data-testid="stWidgetLabel"] p { font-family: var(--mono) !important; text-transform: uppercase; letter-spacing: .16em;
  font-size: .7rem !important; color: var(--muted) !important; }
[data-testid="stSelectbox"] [role="group"], [data-testid="stTextInput"] div:has(> input), [data-testid="stTextArea"] div:has(> textarea) {
  background: var(--panel) !important; border-color: var(--line-strong) !important; border-radius: 8px !important; }
[data-testid="stSelectbox"] input, [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
  background: var(--panel) !important; color: var(--text) !important; -webkit-text-fill-color: var(--text); caret-color: var(--text); }
[data-testid="stSelectbox"] button { color: var(--muted) !important; background: transparent !important; }
[data-testid="stSelectboxVirtualDropdown"], [role="listbox"] { background: var(--panel) !important; color: var(--text) !important; }
[role="listbox"] [role="option"] { color: var(--text) !important; background: transparent !important; }
[role="listbox"] [role="option"][data-focused="true"], [role="listbox"] [role="option"]:hover { background: var(--raise) !important; }
[data-testid="InputInstructions"] { display: none; }
[data-testid="stButtonGroup"] button { background: var(--panel) !important; color: var(--muted) !important; border-color: var(--line-strong) !important; }
[data-testid="stButtonGroup"] button p { font-family: var(--mono) !important; font-size: .74rem !important; letter-spacing: .1em; text-transform: uppercase; }
[data-testid="stButtonGroup"] button[data-selected="true"] { background: var(--accent) !important; color: var(--on-accent) !important; border-color: var(--accent) !important; }
[data-testid="stExpander"] details { background: var(--panel); border-color: var(--line-strong) !important; border-radius: 8px; }
[data-testid="stExpander"] summary * { color: var(--text) !important; }
[data-testid="stAlertContainer"] { background: var(--panel) !important; color: var(--text) !important; border: 1px solid var(--line-strong) !important;
  border-left: 4px solid var(--accent) !important; border-radius: 8px !important; }
[data-testid="stAlertContainer"] * { color: var(--text) !important; fill: var(--text) !important; }
[data-testid="stToast"] { background: var(--panel) !important; border: 1px solid var(--line-strong); }
[data-testid="stToast"] * { color: var(--text) !important; }

/* Bottom menu */
.st-key-bottom_nav { position: fixed; left: 50%; bottom: 1.1rem; transform: translateX(-50%); z-index: 1000;
  width: auto !important; max-width: calc(100vw - 1.5rem); flex-direction: row !important; flex-wrap: nowrap !important;
  align-items: center; gap: 0 !important; padding: .35rem; border-radius: 999px; background: var(--panel);
  border: 1px solid var(--line-strong); box-shadow: 0 12px 34px -10px rgba(60, 35, 15, .45); }
.st-key-bottom_nav > div, .st-key-bottom_nav [data-testid="stLayoutWrapper"], .st-key-bottom_nav [data-testid="stVerticalBlock"],
.st-key-bottom_nav [data-testid="stElementContainer"], .st-key-bottom_nav [data-testid="stPageLink"] { width: auto !important; flex: 0 0 auto !important; }
.st-key-bottom_nav [data-testid="stLayoutWrapper"] + [data-testid="stLayoutWrapper"] { border-left: 1px solid var(--line-strong); }
.st-key-bottom_nav [data-testid="stPageLink-NavLink"] { gap: .55rem; padding: .5rem 1.6rem; margin: 0 .35rem; border-radius: 999px;
  background: transparent !important; text-decoration: none; }
.st-key-bottom_nav [data-testid="stPageLink-NavLink"] * { color: var(--text) !important; }
.st-key-bottom_nav [data-testid="stPageLink-NavLink"] p { font-family: var(--serif) !important; font-size: 1.05rem !important; margin: 0; }
.st-key-bottom_nav [data-testid="stPageLink-NavLink"]:hover { background: var(--raise) !important; }
.st-key-bottom_nav .st-key-nav_current [data-testid="stPageLink-NavLink"] { background: var(--raise) !important;
  box-shadow: 0 0 0 1px var(--line-strong) inset; }
.st-key-bottom_nav .st-key-nav_current [data-testid="stPageLink-NavLink"] [data-testid="stIconMaterial"] { color: var(--accent) !important; }
@media (max-width: 640px) {
  .st-key-bottom_nav [data-testid="stPageLink-NavLink"] { padding: .5rem .7rem; margin: 0 .1rem; flex-direction: column; gap: .1rem; }
  .st-key-bottom_nav [data-testid="stPageLink-NavLink"] p { font-size: .72rem !important; }
}

/* Click-anywhere cards: an invisible Streamlit button stretched over the card */
[class*="st-key-hit_"] { position: relative; }
[class*="st-key-hit_"] .stButton { position: absolute; inset: 0; z-index: 3; }
[class*="st-key-hit_"] .stButton button { width: 100%; height: 100%; opacity: 0; }
[class*="st-key-hit_"] [data-testid="stElementContainer"]:has(.stButton) { position: static; height: 0; margin: 0; }

/* Record sleeve with the vinyl case picker */
.st-key-sleeve { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 12px; padding: 1.5rem 1.25rem 1.25rem;
  box-shadow: var(--shadow); clip-path: polygon(0 0, calc(100% - 46px) 0, 100% 46px, 100% 100%, 0 100%); }
.fx-sleeve-title { font-family: var(--mono); font-weight: 600; font-size: 1.1rem; letter-spacing: .2em; padding-bottom: .4rem;
  border-bottom: 3px solid var(--orange); display: inline-block; }
.fx-sleeve-domain { font-family: var(--display); font-size: 1.45rem; line-height: 1.2; margin-top: .25rem; }
.st-key-vinyl { position: relative; width: 250px !important; min-width: 250px; height: 250px; margin: 1.25rem auto .75rem; flex: none; }
.st-key-vinyl > [data-testid="stElementContainer"]:first-child { position: absolute; inset: 0; }
.fx-disc { position: absolute; inset: 0; border-radius: 50%;
  background: radial-gradient(circle, transparent 0 34%, rgba(255,255,255,.06) 34.5%, transparent 35%),
              repeating-radial-gradient(circle, #121110 0 2px, #1d1b19 2px 4px);
  box-shadow: 0 18px 40px -16px rgba(0,0,0,.6), inset 0 0 0 6px #0d0c0b; }
.fx-disc::after { content: ''; position: absolute; inset: 0; border-radius: 50%;
  background: linear-gradient(135deg, rgba(255,255,255,.14), transparent 35%, transparent 65%, rgba(255,255,255,.07)); }
.fx-disc .ring { position: absolute; inset: 28px; border-radius: 50%; border: 1.5px solid rgba(239, 229, 212, .55); }
.fx-disc .label { position: absolute; inset: 66px; border-radius: 50%; background: var(--label); display: grid; place-items: center;
  text-align: center; color: #2a1c12; box-shadow: inset 0 0 0 3px rgba(0,0,0,.08); }
.fx-disc .label b { font-family: var(--display); font-weight: 400; font-size: 1.3rem; line-height: 1; display: block; }
.fx-disc .label i { display: block; width: 12px; height: 12px; margin: .45rem auto 0; border-radius: 50%; background: #1a1714; }
[class*="st-key-vinyl_"] { position: absolute !important; width: 42px !important; z-index: 2; }
[class*="st-key-vinyl_"] button { width: 42px; height: 42px; min-height: 42px; padding: 0; border-radius: 50%;
  background: var(--label) !important; color: #2a1c12 !important; border: 2px solid #1a1714 !important; }
[class*="st-key-vinyl_"] button p { font-family: var(--serif) !important; font-weight: 700; font-size: 1rem !important; }
[class*="st-key-vinyl_"] button:hover { background: #fff !important; }
[class*="st-key-vinylon_"] { position: absolute !important; width: 48px !important; z-index: 2; }
[class*="st-key-vinylon_"] button { width: 48px; height: 48px; min-height: 48px; padding: 0; border-radius: 50%;
  background: var(--orange) !important; color: #fff !important; border: 3px solid #1a1714 !important; }
[class*="st-key-vinylon_"] button p { font-family: var(--serif) !important; font-weight: 700; font-size: 1.1rem !important; }
.st-key-case_steps { border-top: 1px solid var(--line); padding-top: .85rem; margin-top: .5rem; }
.st-key-case_steps button { border: none !important; background: transparent !important; padding: .25rem .2rem; }
.st-key-case_steps button p { font-size: .95rem !important; }

/* Case file folder */
.fx-folder-tab { display: inline-block; background: var(--panel2); border: 1px solid var(--line-strong); border-bottom: none;
  border-radius: 12px 26px 0 0; padding: .65rem 3.2rem .55rem 1.6rem; font-family: var(--mono); font-weight: 600;
  letter-spacing: .26em; font-size: .9rem; position: relative; top: 1px; z-index: 1; }
.st-key-folder { background: var(--panel2); border: 1px solid var(--line-strong); border-radius: 0 14px 14px 14px;
  padding: 1.5rem 1.75rem 1.5rem; box-shadow: var(--shadow); }
@media (min-width: 1100px) { .fx-file-head { flex-wrap: nowrap !important; } }
.fx-file-head { display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap; padding-bottom: 1.1rem; border-bottom: 1px solid var(--line-strong); }
.fx-file-head h2 { margin: 0 !important; padding: 0 !important; font-size: clamp(1.9rem, 2.9vw, 2.9rem) !important; line-height: 1.05; flex: 1 1 auto; min-width: 0; }
.fx-file-head .score { font-family: var(--display); color: var(--accent); font-size: clamp(2.8rem, 4.6vw, 4.3rem); line-height: 1;
  border-left: 1px solid var(--line-strong); border-right: 1px solid var(--line-strong); padding: 0 1.75rem; white-space: nowrap; }
.fx-file-head .score small { font-family: var(--serif); color: var(--text); font-size: .38em; }

/* Evidence tab cards */
[class*="st-key-hit_tab"] { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 8px; padding: .55rem .6rem .5rem;
  box-shadow: 0 6px 14px -10px rgba(60,35,15,.5); transition: transform .15s ease; }
[class*="st-key-hit_tab"]:hover { transform: translateY(-3px) rotate(-.4deg); }
[class*="st-key-hit_tabon"] { border: 2px solid var(--accent); transform: rotate(-1.2deg); box-shadow: 0 10px 20px -10px rgba(156,69,32,.55); }
.fx-tabcard .n { font-family: var(--serif); font-weight: 700; font-size: .95rem; }
.fx-tabcard .thumb { height: 74px; border-radius: 4px; overflow: hidden; margin: .3rem 0 .4rem; background: var(--raise);
  display: grid; place-items: center; border: 1px solid var(--line); }
.fx-tabcard .thumb img { width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }
.fx-tabcard .t { text-align: center; font-family: var(--serif); font-weight: 600; font-size: 1rem; }
.fx-mini-url { background: #fff; color: #222; border-radius: 6px; padding: .35rem .55rem; font-family: Arial, sans-serif; font-size: .74rem;
  box-shadow: 0 1px 3px rgba(0,0,0,.18); max-width: 92%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fx-mini-chip { background: #fff; color: #1d1d1d; border-radius: 6px; padding: .3rem .6rem; font-family: Arial, sans-serif; font-weight: 800;
  font-style: italic; font-size: .82rem; line-height: 1.15; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,.18); max-width: 92%; }
.fx-mini-date { background: #fff; color: #222; border-radius: 6px; padding: .35rem .55rem; font-family: Arial, sans-serif; font-size: .72rem;
  box-shadow: 0 1px 3px rgba(0,0,0,.18); }
[class*="st-key-tabnav_"] button { width: 42px; height: 42px; min-height: 42px; border-radius: 50%; padding: 0; margin-top: 2.4rem;
  border: 1.5px solid var(--text) !important; background: var(--panel) !important; }

/* Website panels */
.fx-site { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 8px; padding: .6rem; box-shadow: var(--shadow); }
.fx-site-head { display: flex; align-items: center; gap: .9rem; margin-bottom: .55rem; }
.fx-pill { color: #fff; border-radius: 6px 6px 6px 0; padding: .22rem .85rem; font-family: var(--serif); font-size: 1rem; white-space: nowrap; }
.fx-pill.ok { background: var(--green); } .fx-pill.bad { background: var(--red); }
.fx-site-head span.u { font-family: Arial, sans-serif; font-size: .82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
.fx-site img { width: 100%; display: block; border-radius: 4px; border: 1px solid var(--line); }

/* Reasons and verdict column */
.fx-side { border-left: 1px solid var(--line-strong); padding-left: 1.4rem; height: 100%; }
.fx-side .fx-label { display: block; margin-bottom: .7rem; }
.fx-reasons { list-style: none; padding: 0 !important; margin: 0 0 1.4rem !important; }
.fx-reasons li { position: relative; padding-left: 1.6rem; margin: 0 0 .45rem !important; font-size: 1.08rem; line-height: 1.35; }
.fx-reasons li::before { content: ''; position: absolute; left: 0; top: .42em; width: .62em; height: .62em; background: var(--accent); }
.fx-side .verdict { border-top: 1px solid var(--line-strong); padding-top: 1.1rem; font-size: 1.08rem; line-height: 1.55; }
.fx-side .verdict b { font-family: var(--display); font-weight: 400; font-size: 1.35rem; display: block; margin-bottom: .2rem; }

/* Owner decision */
.st-key-decision { border-top: 1px solid var(--line-strong); padding-top: 1.1rem; margin-top: .6rem; }
.st-key-decision .stButton button { height: 3.1rem; border: 1.5px solid var(--text) !important; background: transparent !important; }
.st-key-decision .stButton button p { font-family: var(--display) !important; font-size: 1.6rem !important; }
.st-key-decision [data-testid="stBaseButton-primary"] { background: var(--accent) !important; color: var(--on-accent) !important;
  border-color: var(--accent) !important; box-shadow: 0 6px 16px -8px rgba(156,69,32,.8); }

/* Phrases, domain, image matches */
.fx-phrase { display: grid; grid-template-columns: 11rem 1fr; gap: 1rem; padding: .85rem .25rem; border-bottom: 1px solid var(--line); align-items: baseline; }
.fx-phrase b { font-family: var(--display); font-weight: 400; font-size: 1.25rem; }
.fx-phrase span { color: var(--muted); }
.fx-phrase mark { background: transparent; color: var(--accent); font-weight: 700; border-bottom: 2px solid var(--accent); }
.fx-age { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 10px; padding: 1.1rem 1.25rem; height: 100%; box-shadow: var(--shadow); }
.fx-age .k { font-family: var(--mono); font-size: .7rem; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); }
.fx-age .v { font-family: var(--display); font-size: 2.3rem; line-height: 1.15; margin: .2rem 0; }
.fx-age .d { font-family: var(--mono); font-size: .74rem; color: var(--muted); }
.fx-match-kind { font-family: var(--mono); font-size: .74rem; letter-spacing: .06em; color: var(--accent); font-weight: 600; }
[data-testid="stImage"] img { border: 1px solid var(--line-strong); border-radius: 6px; background: var(--panel); }
[data-testid="stImageCaption"], [data-testid="caption"] { font-family: var(--mono) !important; text-transform: uppercase; letter-spacing: .12em;
  font-size: .68rem !important; color: var(--muted) !important; }

/* Verdict */
.fx-verdict { background: var(--panel); border: 1px solid var(--line-strong); border-left: 5px solid var(--accent); border-radius: 10px; padding: 1.5rem 1.75rem; box-shadow: var(--shadow); }
.fx-verdict .v { font-family: var(--display); font-size: clamp(1.9rem, 4vw, 2.7rem); line-height: 1.1; color: var(--accent); }
.fx-verdict p { font-size: 1.15rem; line-height: 1.6; margin: .8rem 0 0; max-width: 820px; }
.fx-conf { display: flex; align-items: center; gap: .8rem; margin-top: .9rem; max-width: 420px; }
.fx-conf b { font-family: var(--mono); font-size: .8rem; font-weight: 500; white-space: nowrap; }
.fx-bar { height: 5px; background: var(--track); border-radius: 3px; overflow: hidden; flex: 1; }
.fx-bar i { display: block; height: 100%; background: var(--accent); }
.fx-finding { display: grid; grid-template-columns: 2.2rem 1fr auto; gap: 1rem; padding: .9rem .25rem; border-bottom: 1px solid var(--line); align-items: start; }
.fx-finding .n { font-family: var(--mono); color: var(--accent); font-size: .85rem; padding-top: .2rem; }
.fx-finding b { display: block; font-size: 1.1rem; }
.fx-finding span.d { color: var(--muted); line-height: 1.5; }
.fx-tag { display: inline-block; font-family: var(--mono); font-size: .66rem; text-transform: uppercase; letter-spacing: .12em;
  padding: .25rem .55rem; border: 1px solid var(--line-strong); border-radius: 4px; white-space: nowrap; }
.fx-tag.solid { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.fx-action { background: var(--panel); border: 1.5px dashed var(--accent); border-radius: 10px; padding: 1.1rem 1.25rem; font-size: 1.15rem; }

/* Cases page */
[class*="st-key-hit_case"] { background: var(--panel); border: 1px solid var(--line-strong); border-radius: 12px; padding: .8rem;
  box-shadow: var(--shadow); transition: transform .15s ease; }
[class*="st-key-hit_case"]:hover { transform: translateY(-4px) rotate(-.5deg); }
.fx-casecard .shot { height: 170px; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); }
.fx-casecard .shot img { width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }
.fx-casecard .meta { display: flex; justify-content: space-between; align-items: center; margin: .8rem .2rem .2rem; }
.fx-casecard .dom { font-family: var(--display); font-size: 1.3rem; line-height: 1.2; margin: .15rem .2rem .5rem; }
.fx-casecard .score { font-family: var(--display); color: var(--accent); font-size: 2.2rem; line-height: 1; }
.fx-casecard .score small { font-family: var(--serif); color: var(--text); font-size: .45em; }
.fx-casecard .facts { display: flex; gap: .4rem; flex-wrap: wrap; margin: 0 .2rem .2rem; }

/* Tables */
.fx-table-wrap { border: 1px solid var(--line-strong); border-radius: 10px; overflow-x: auto; background: var(--panel); box-shadow: var(--shadow); }
.fx-table { width: 100%; border-collapse: collapse; font-size: 1rem; color: var(--text); margin: 0 !important; }
.fx-table th { text-align: left; font-family: var(--mono); font-size: .66rem; font-weight: 600; text-transform: uppercase; letter-spacing: .16em;
  color: var(--muted); padding: .8rem 1rem; border: none !important; border-bottom: 1px solid var(--line-strong) !important; background: var(--panel); white-space: nowrap; }
.fx-table td { padding: .75rem 1rem; border: none !important; border-bottom: 1px solid var(--line) !important; white-space: nowrap; }
.fx-table tr:last-child td { border-bottom: none !important; }
.fx-table .id { font-family: var(--mono); color: var(--muted); }
.fx-table .meter { display: flex; align-items: center; gap: .6rem; min-width: 140px; }
.fx-table .meter b { font-family: var(--mono); font-weight: 500; font-size: .8rem; width: 2ch; text-align: right; }
</style>
"""

# Each mode is a set of CSS variables; STYLE only refers to the variables.
MODES = {
    'Light': dict(bg='#f1e9dc', panel='#f9f3e8', panel2='#f5ecde', raise_='#ecdfca', line='#e0d2bb', line_strong='#bfac8e',
                  text='#2a1c12', muted='#6f5d4b', dot='#e5d9c5', track='#e3d5bf', accent='#9c4520', on_accent='#fbf4e8',
                  orange='#e1662a', green='#4f7a35', red='#c43a2e', label='#f3ead9', glow='rgba(255,255,255,.35)'),
    'Grey': dict(bg='#2b2824', panel='#353029', panel2='#38332c', raise_='#433c33', line='#4a433a', line_strong='#6b6155',
                 text='#f0e7da', muted='#b9ad9d', dot='#332f2a', track='#4a433a', accent='#d27442', on_accent='#1d1a17',
                 orange='#e8692c', green='#6f9a50', red='#d4524a', label='#efe5d4', glow='transparent'),
    'Dark': dict(bg='#14110f', panel='#1d1916', panel2='#211c18', raise_='#2b2520', line='#2f2924', line_strong='#4f453b',
                 text='#f1e7d8', muted='#a89a88', dot='#1b1714', track='#2f2924', accent='#d27442', on_accent='#14110f',
                 orange='#e8692c', green='#6f9a50', red='#d4524a', label='#efe5d4', glow='transparent'),
}
DEFAULT_MODE = 'Light'


def _mode_css(mode):
    tokens = ' '.join(f"--{k.rstrip('_').replace('_', '-')}: {v};" for k, v in MODES[mode].items())
    return f'<style>:root {{ {tokens} color-scheme: {"light" if mode == "Light" else "dark"}; }}</style>'


def _remember_mode():
    # Widget state is per page, so copy the choice to a key that survives page switches.
    st.session_state['mode'] = st.session_state['_mode_widget'] or st.session_state.get('mode', DEFAULT_MODE)
    storage.save_profile(appearance=st.session_state['mode'])


def appearance_control(container=st):
    st.session_state['_mode_widget'] = st.session_state.get('mode', DEFAULT_MODE)
    container.segmented_control('Appearance', list(MODES), key='_mode_widget', on_change=_remember_mode)


def base(title, layout='wide'):
    """Page config and styling. Every page calls this first (via shell or setup)."""
    st.set_page_config(page_title=f'{title} — FORGE', page_icon='◼', layout=layout)
    storage.refresh_state_file()
    try:
        profile = storage.load_profile()
    except (OSError, sqlite3.Error):
        profile = dict(storage.PROFILE_DEFAULTS)
    if 'mode' not in st.session_state:
        st.session_state['mode'] = profile['appearance'] if profile['appearance'] in MODES else DEFAULT_MODE
    st.markdown(_mode_css(st.session_state['mode']) + STYLE, unsafe_allow_html=True)
    return profile


# (page file, label, icon, page title it belongs to)
NAV = (('views/cases.py', 'Cases', ':material/folder_open:', 'Cases'),
       ('views/evidence.py', 'Evidence', ':material/layers:', 'Evidence'),
       ('views/reports.py', 'Reports', ':material/bar_chart:', 'Report tracker'),
       ('views/brand_protection.py', 'Brand', ':material/verified_user:', 'Brand protection'),
       ('views/settings.py', 'Settings', ':material/settings:', 'Settings'))


def _bottom_menu(title):
    """Floating pill menu fixed to the bottom of the screen."""
    with st.container(key='bottom_nav'):
        for path, label, icon, page_title in NAV:
            # The current page's link sits in a keyed container so CSS can highlight it.
            with st.container(key='nav_current' if page_title == title else None):
                st.page_link(path, label=label, icon=icon)


def header(stamp=None):
    right = f'<div class="right"><span class="fx-stamp">{escape(stamp)}</span></div>' if stamp else ''
    st.markdown(f'<div class="fx-header"><span class="logo">FORGE</span><span class="sub">The evidence collection</span>{right}</div>',
                unsafe_allow_html=True)


def shell(title, lede=None, stamp=None, show_title=True):
    """Page frame: header, bottom menu, title. Returns the workspace profile."""
    profile = base(title)
    header(stamp)
    _bottom_menu(title)
    if show_title:
        st.title(title, anchor=False)
        if lede:
            st.markdown(f'<p class="fx-lede">{escape(lede)}</p>', unsafe_allow_html=True)
    return profile


def load_cases_or_stop():
    try:
        cases = storage.load_cases()
    except FileNotFoundError:
        st.info(f'No state file yet at {storage.STATE_FILE}. Run the FORGE engine (`python main.py --store <url>`) '
                'or the demo (`python analyze.py`), or point FORGE_STATE_FILE at a ForgeState JSON file.')
        st.stop()
    except (ValueError, OSError, sqlite3.Error) as exc:
        st.error(f'Unable to load cases: {exc}')
        st.stop()
    if not cases:
        st.info('No suspects to review yet. Run the FORGE engine (`python main.py --store <your store URL>` '
                'from the repository root) or the demo (`python analyze.py`).')
        st.stop()
    source_note()
    return cases


def source_note():
    """Say where the cases come from: the FORGE engine's results or the demo data."""
    source = storage.state_source()
    if source == 'engine':
        when = datetime.fromtimestamp(storage.STATE_FILE.stat().st_mtime).strftime('%d %b %Y %H:%M')
        note(f'Results from the FORGE engine · cache/state.json · updated {when}')
    elif source == 'demo':
        note('Demo data (synthetic Northstar cases). Run `python main.py --store <your store URL>` from the '
             'repository root and this dashboard switches to the FORGE engine\'s results.')
    else:
        note(f'Results from {storage.STATE_FILE}')


def setup(title, lede=None):
    profile = shell(title, lede)
    return load_cases_or_stop(), profile


def section(label, number=None):
    num = f'<span class="n">{number:02d}</span>' if number is not None else ''
    st.markdown(f'<div class="fx-section">{num}{escape(label)}</div>', unsafe_allow_html=True)


def note(text):
    st.markdown(f'<div class="fx-note">{escape(text)}</div>', unsafe_allow_html=True)


def selected_case(cases):
    ids = [c['case_id'] for c in cases]
    if st.session_state.get('selected_case_id') not in ids:
        st.session_state['selected_case_id'] = ids[0]
    return next(c for c in cases if c['case_id'] == st.session_state['selected_case_id'])


def choose_case(cases, key):
    ids = [c['case_id'] for c in cases]
    labels = {c['case_id']: c['domain'] for c in cases}
    current = selected_case(cases)['case_id']
    selected = st.selectbox('Select a case', ids, index=ids.index(current),
                            format_func=lambda cid: f'Case {cid:03d} · {labels[cid]}', key=key)
    st.session_state['selected_case_id'] = selected
    return next(c for c in cases if c['case_id'] == selected)


# --- Facts about a case ---------------------------------------------------

def age_text(days):
    """'9 days', '1 year 2 months', '22 years' for an age in days; None if unknown."""
    if days is None or days < 0:
        return None
    if days < 60:
        return f"{days} day{'s' * (days != 1)}"
    years, months = divmod(int(days / 30.4375), 12)
    if years == 0:
        return f'{months} months'
    if years >= 5 or months == 0:
        return f"{years} year{'s' * (years != 1)}"
    return f"{years} year{'s' * (years != 1)} {months} month{'s' * (months != 1)}"


def age_card(label, days):
    age = age_text(days)
    if age:
        tag = ' <span class="fx-stamp small" style="vertical-align:middle;margin-left:.6rem">New</span>' if days < 30 else ''
        when = (date.today() - timedelta(days=days)).strftime('%d %b %Y').lstrip('0')
        body = f'<div class="v">{age}{tag}</div><div class="d">Registered around {when}</div>'
    else:
        body = '<div class="v">Unknown</div><div class="d">No domain age supplied</div>'
    st.markdown(f'<div class="fx-age"><div class="k">{escape(label)}</div>{body}</div>', unsafe_allow_html=True)


def risk_label(score):
    return 'High' if score >= 70 else 'Medium' if score >= 40 else 'Low'


def breakable(domain):
    """Escape a domain and let it wrap only after dots and hyphens."""
    return escape(domain).replace('.', '.<wbr>').replace('-', '-<wbr>')


def file_number(case):
    return f"FG-{case['case_id']:05d}"


def summary(cases):
    rows = ''.join(
        f'<tr><td class="id">{c["case_id"]:03d}</td><td>{escape(c["domain"])}</td>'
        f'<td><div class="meter"><div class="fx-bar"><i style="width:{c["risk_score"]:g}%"></i></div>'
        f'<b>{c["risk_score"]:g}</b></div></td><td>{risk_label(c["risk_score"])}</td>'
        f'<td>{age_text(c.get("domain_age_days")) or "—"}</td>'
        f'<td>{escape(c["decision"].title())}</td><td>{escape(c["report_status"].title())}</td></tr>'
        for c in cases)
    head = ''.join(f'<th>{h}</th>' for h in ('Case', 'Domain', 'Risk', 'Band', 'Site age', 'Decision', 'Report'))
    st.markdown(f'<div class="fx-table-wrap"><table class="fx-table"><thead><tr>{head}</tr></thead>'
                f'<tbody>{rows}</tbody></table></div>', unsafe_allow_html=True)


# --- Images ---------------------------------------------------------------

def evidence_path(filename):
    # Local files only; relative paths are relative to the cases JSON file, else to the repository root
    # (where the FORGE engine writes output/).
    path = Path(filename)
    if path.is_absolute():
        return path
    beside = storage.STATE_FILE.parent / path
    return beside if beside.exists() or not (storage.REPO / path).exists() else storage.REPO / path


@st.cache_data(show_spinner=False, max_entries=256)
def _image_uri(path, mtime, width, top):
    image = Image.open(io.BytesIO(raster_bytes(Path(path).read_bytes()))).convert('RGB')  # SVG is rendered
    if top:
        image = image.crop((0, min(top, image.height - 1), image.width, image.height))
    if width and image.width > width:
        image = image.resize((width, round(image.height * width / image.width)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=85, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()


def image_uri(filename, width=900, top=0):
    """An evidence image as a data URI for use inside HTML, or None if it is missing or unreadable."""
    path = evidence_path(filename)
    try:
        return _image_uri(str(path), path.stat().st_mtime, width, top)
    except (OSError, ValueError):
        return None


def evidence_image(filename, caption):
    path = evidence_path(filename)
    if not path.is_file():
        st.warning(f'{caption}: evidence file is missing.')
        return
    try:
        st.image(str(path), caption=caption, use_container_width=True)
    except Exception:
        st.warning(f'{caption}: this evidence image could not be displayed.')


# --- Verdict --------------------------------------------------------------

POINTS_TO = {'impersonation': 'Points to impersonation', 'legitimate': 'Points to legitimate', 'neutral': 'Neutral'}


def verdict_card(saved):
    result = saved['result']
    conf = int(result['confidence'])
    st.markdown(f'''<div class="fx-verdict"><div class="fx-label muted">Gemini verdict</div>
<div class="v">{escape(result['verdict'])}</div>
<div class="fx-conf"><div class="fx-bar"><i style="width:{conf}%"></i></div><b>{conf}% confident</b></div>
<p>{escape(result['summary'])}</p></div>''', unsafe_allow_html=True)
    note(f"{saved['model']} · {saved['created_at']} UTC")
    section('Findings')
    rows = ''.join(
        f'<div class="fx-finding"><span class="n">{i:02d}</span><div><b>{escape(f["title"])}</b>'
        f'<span class="d">{escape(f["detail"])}</span></div>'
        f'<span class="fx-tag{" solid" if f["points_to"] == "impersonation" else ""}">'
        f'{POINTS_TO.get(f["points_to"], escape(f["points_to"]))}</span></div>'
        for i, f in enumerate(result['findings'], 1))
    st.markdown(f'<div style="border-top:1px solid var(--line-strong)">{rows}</div>', unsafe_allow_html=True)
    section('Recommended action')
    st.markdown(f'<div class="fx-action">{escape(result["recommended_action"])}</div>', unsafe_allow_html=True)


# --- Saving ---------------------------------------------------------------

def persist(case_id, **fields):
    try:
        note = storage.save_review(case_id, **fields)
    except (sqlite3.Error, OSError, ValueError) as exc:
        st.error(f'Could not save: {exc}')
        return
    st.session_state['save_notice'] = f'Saved successfully. {note}'.strip()
    st.rerun()


def notice():
    if 'save_notice' in st.session_state:
        st.toast(st.session_state.pop('save_notice'), icon=':material/check:')
