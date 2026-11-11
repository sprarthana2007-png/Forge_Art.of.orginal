import streamlit as st

# Navigation is the floating menu at the bottom of every page (see ui.py).
st.navigation([
    st.Page('views/cases.py', title='Cases', default=True),
    st.Page('views/evidence.py', title='Evidence', url_path='evidence'),
    st.Page('views/reports.py', title='Reports', url_path='reports'),
    st.Page('views/settings.py', title='Settings', url_path='settings'),
], position='hidden').run()
