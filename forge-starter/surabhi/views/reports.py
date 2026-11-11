import streamlit as st
from storage import REPORT_STATUSES
import ui

cases, _ = ui.setup('Report tracker', lede='Track reports manually. Changing a status does not send a report.')
ui.notice()
for column, status in zip(st.columns(4), REPORT_STATUSES):
    column.metric(status.title(), sum(c['report_status'] == status for c in cases))
ui.section('All cases', 1)
ui.summary(cases)
ui.section('Update status', 2)
case = ui.choose_case(cases, 'report_picker')
with st.form(f"report_{case['case_id']}_{case['report_status']}"):
    status = st.selectbox('Report status', REPORT_STATUSES, index=REPORT_STATUSES.index(case['report_status']), format_func=str.title)
    if st.form_submit_button('Save report status', type='primary'):
        ui.persist(case['case_id'], report_status=status)
