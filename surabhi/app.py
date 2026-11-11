# Load .env (project root) before anything else reads os.environ; real environment variables win.
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env', encoding='utf-8-sig')

import streamlit as st

# Navigation is the floating menu at the bottom of every page (see ui.py).
st.navigation([
    st.Page('views/cases.py', title='Cases', default=True),
    st.Page('views/evidence.py', title='Evidence', url_path='evidence'),
    st.Page('views/reports.py', title='Reports', url_path='reports'),
    st.Page('views/brand_protection.py', title='Brand protection', url_path='brand-protection'),
    st.Page('views/settings.py', title='Settings', url_path='settings'),
], position='hidden').run()
