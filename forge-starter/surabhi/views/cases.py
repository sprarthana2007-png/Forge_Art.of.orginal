from html import escape
import streamlit as st
import storage
import ui

BROWSER_BAR = 76

cases, profile = ui.setup('Cases')
brand_name = (storage.load_brand() or {}).get('brand_name') or profile['brand_name']
st.markdown(f'<p class="fx-lede">Every website that may be impersonating {escape(brand_name)}. '
            'Open a case file to see the evidence.</p>', unsafe_allow_html=True)
a, b, c, d = st.columns(4)
a.metric('Case files', len(cases))
b.metric('High risk', sum(x['risk_score'] >= 70 for x in cases))
c.metric('Awaiting decision', sum(x['decision'] == 'pending' for x in cases))
d.metric('Reports sent', sum(x['report_status'] in ('sent', 'resolved') for x in cases))


def open_case(case_id):
    st.session_state['selected_case_id'] = case_id
    st.session_state['evidence_tab'] = 'Overview'
    st.session_state['_go_evidence'] = True


ui.section('Case files', 1)
for start in range(0, len(cases), 4):
    columns = st.columns(4, gap='medium')
    for column, case in zip(columns, cases[start:start + 4]):
        uri = ui.image_uri(case['fake_screenshot'], width=520, top=BROWSER_BAR)
        shot = f'<img src="{uri}" alt="">' if uri else ''
        age = ui.age_text(case.get('domain_age_days'))
        facts = (f'<span class="fx-tag">{escape(case["decision"].title())}</span>'
                 f'<span class="fx-tag">{escape(case["report_status"].title())}</span>'
                 + (f'<span class="fx-tag">{age} old</span>' if age else ''))
        with column, st.container(key=f'hit_case_{case["case_id"]}'):
            st.markdown(f'''<div class="fx-casecard"><div class="shot">{shot}</div>
<div class="meta"><span class="fx-label">Case {case["case_id"]:03d}</span><span class="fx-stamp small">{ui.risk_label(case["risk_score"])} risk</span></div>
<div class="dom">{ui.breakable(case["domain"])}</div>
<div class="meta" style="margin-top:0"><span class="score">{case["risk_score"]:g}<small>/100</small></span></div>
<div class="facts">{facts}</div></div>''', unsafe_allow_html=True)
            st.button(f'Open case {case["case_id"]}', key=f'open_{case["case_id"]}', on_click=open_case, args=(case['case_id'],))

if st.session_state.pop('_go_evidence', False):
    st.switch_page('views/evidence.py')

ui.section('All cases at a glance', 2)
ui.summary(cases)
