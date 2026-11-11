import streamlit as st
import gemini
import storage
import ui

profile = ui.shell('Settings',
                   lede='The brand you protect, your business details and how FORGE looks.')
ui.notice()


def save(**fields):
    try:
        storage.save_profile(**fields)
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        return
    st.session_state['save_notice'] = 'Saved successfully.'
    st.rerun()


ui.section('Brand', 1)
ui.note('Gemini uses these details to judge whether a site is impersonating you.')
with st.form('brand'):
    a, b = st.columns(2)
    brand_name = a.text_input('Brand you protect *', profile['brand_name'])
    website = b.text_input('Official website', profile['website'], placeholder='northstar.example')
    options = storage.INDUSTRIES if profile['industry'] in storage.INDUSTRIES else (*storage.INDUSTRIES, profile['industry'])
    industry = st.selectbox('Industry', options, index=options.index(profile['industry']),
                            format_func=lambda v: v or 'Choose…')
    if st.form_submit_button('Save brand', type='primary'):
        save(brand_name=brand_name, website=website, industry=industry)

ui.section('Business details', 2)
with st.form('business'):
    a, b = st.columns(2)
    business_name = a.text_input('Registered business name', profile['business_name'])
    tax_id = b.text_input('Tax / registration number', profile['tax_id'], placeholder='GSTIN, VAT or company number')
    a, b, c = st.columns(3)
    contact_name = a.text_input('Contact person', profile['contact_name'])
    contact_email = b.text_input('Contact email', profile['contact_email'], placeholder='legal@company.com')
    contact_phone = c.text_input('Contact phone', profile['contact_phone'])
    address = st.text_area('Registered address', profile['address'], height=90)
    country = st.text_input('Country', profile['country'])
    if st.form_submit_button('Save business details', type='primary'):
        save(business_name=business_name, tax_id=tax_id, contact_name=contact_name, contact_email=contact_email,
             contact_phone=contact_phone, address=address, country=country)

ui.section('Appearance', 3)
ui.appearance_control()

ui.section('Gemini', 4)
if gemini.missing_packages():
    st.warning(f"Missing Python packages: {', '.join(gemini.missing_packages())}. "
               'Run `python -m pip install -r requirements.txt`, then restart the app.')
elif gemini.api_key_configured():
    ui.note(f'Connected · model {gemini.model_name()}')
else:
    st.info('Gemini is not connected. Set GEMINI_API_KEY before starting the app to get verdicts. '
            'Optionally set GEMINI_MODEL to choose the model.')
