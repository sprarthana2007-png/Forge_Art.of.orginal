"""Ask Google Gemini for a verdict on a case.

The case's evidence is packed into one JSON "investigation" document and sent to
Gemini together with the official and suspected screenshots, through the
Google Gen AI SDK (`google-genai`). Gemini answers with JSON that follows the
`Verdict` schema below, so the app can show it without parsing free text.

Set GEMINI_API_KEY (or GOOGLE_API_KEY) before starting the app.
GEMINI_MODEL picks the model; it defaults to DEFAULT_MODEL.
"""
import json
import os
from datetime import date
from pathlib import Path
import importlib.util
from typing import Literal

import storage

try:  # Installed with google-genai. Missing packages disable verdicts instead of crashing the app.
    from pydantic import BaseModel, Field
except ImportError:
    BaseModel = None

DEFAULT_MODEL = 'gemini-flash-latest'
VERDICTS = ('Likely impersonation', 'Suspicious', 'Likely legitimate', 'Inconclusive')

SYSTEM_INSTRUCTION = """You are a brand-protection analyst. A brand owner wants to know whether a website \
impersonates their brand. You receive an investigation in JSON plus two screenshots: image 1 is the brand's \
official website and image 2 is the suspected website.

Weigh every piece of evidence yourself; the risk score and flagged reasons come from automated checks and \
can be wrong. Signs of impersonation include copied logos or product photos, the brand name used without \
authorisation, a very new domain, unrealistic discounts, payment only by untraceable methods, fees to claim \
prizes, and urgency pressure. Signs of legitimacy include an established domain, a verifiable business \
identity, normal prices, several payment and return options, and a clear reseller relationship.

Be specific: cite the evidence behind each finding. Do not invent facts that are not in the investigation or \
screenshots. If the evidence is not enough to decide, say "Inconclusive" and explain what to check next."""


if BaseModel is not None:
    class Finding(BaseModel):
        title: str = Field(description='Short name of the finding, under 8 words.')
        detail: str = Field(description='One or two sentences citing the specific evidence.')
        points_to: Literal['impersonation', 'legitimate', 'neutral']


    class Verdict(BaseModel):
        verdict: Literal['Likely impersonation', 'Suspicious', 'Likely legitimate', 'Inconclusive']
        confidence: int = Field(description='How sure you are of the verdict, 0 to 100.')
        summary: str = Field(description='Two or three sentences a brand owner can act on.')
        findings: list[Finding] = Field(description='The 3 to 6 most important findings, strongest first.')
        recommended_action: str = Field(description='The single next step for the brand owner.')


def missing_packages():
    """Python packages Gemini verdicts need that are not installed."""
    missing = []
    for package, module in (('google-genai', 'google.genai'), ('pydantic', 'pydantic')):
        try:
            found = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:  # parent package (e.g. 'google') missing
            found = False
        if not found:
            missing.append(package)
    return missing


def api_key_configured():
    return bool(os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY'))


def model_name():
    return os.getenv('GEMINI_MODEL') or DEFAULT_MODEL


def build_investigation(case, profile, brand=None, today=None):
    """Everything Gemini needs to know about one case, as a JSON-serialisable dict."""
    brand = brand or {}
    return {
        'brand': {
            'name': brand.get('brand_name') or profile.get('brand_name', ''),
            'official_store': brand.get('store_url') or profile.get('website', ''),
            'verified_owner': brand.get('verified_owner', False),
            'official_domain_age_days': case.get('official_domain_age_days'),
            'official_products': brand.get('product_names', [])[:30],
            'official_prices': brand.get('prices', {}),
            'allow_list': brand.get('allow_list', []),
            'business_name': profile.get('business_name', ''),
            'industry': profile.get('industry', ''),
            'country': profile.get('country', ''),
        },
        'suspect': {
            'url': case.get('url'),
            'domain': case['domain'],
            'found_by': case.get('found_by'),
            'domain_age_days': case.get('domain_age_days'),
            'ip': case.get('ip'),
            'page_title': case.get('page_title'),
            'page_text_excerpt': case.get('page_text'),
            'scam_phrases': case.get('scam_phrases', []),
            'prices_found': case.get('prices_found', {}),
            'contacts_masked': case.get('contacts_masked', []),
            'campaign_id': case.get('campaign_id'),
        },
        'automated_checks': {
            'score': case['risk_score'],
            'label': case.get('label'),
            'reasons': case['reasons'],
            'logo_copied': case.get('logo_match', False),
            'exact_image_copies': len(case.get('image_exact_matches', [])),
            'near_duplicate_images': len(case.get('image_similar_matches', [])),
            'image_matches': [{'official_image': Path(o).name, 'suspect_image': Path(s).name, 'match': kind}
                              for o, s, kind in case.get('matched_images', [])],
            'pipeline_verdict': case.get('pipeline_verdict') or None,
        },
        'owner_review': {'decision': case.get('decision', 'pending')},
        'screenshots': {'image_1': 'official website', 'image_2': 'suspected website'},
        'investigation_date': (today or date.today()).isoformat(),
    }


def _screenshot_parts(case, types):
    parts = []
    for key, label in (('official_screenshot', 'Image 1: official website'),
                       ('fake_screenshot', 'Image 2: suspected website')):
        path = Path(case[key])
        if not path.is_absolute():
            path = storage.STATE_FILE.parent / path
        if path.is_file():
            mime = 'image/jpeg' if path.suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
            parts += [label, types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)]
        else:
            parts.append(f'{label}: not available.')
    return parts


def get_verdict(case, profile, brand=None, client=None, model=None):
    """Send the investigation to Gemini. Returns (investigation, verdict dict, model used)."""
    from google import genai
    from google.genai import types

    model = model or model_name()
    client = client or genai.Client()
    investigation = build_investigation(case, profile, brand)
    contents = ['Investigation (JSON):\n' + json.dumps(investigation, indent=2, ensure_ascii=False),
                *_screenshot_parts(case, types)]
    response = client.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.2,
                                           response_mime_type='application/json', response_schema=Verdict))
    parsed = response.parsed if isinstance(getattr(response, 'parsed', None), Verdict) else None
    if parsed is None:
        if not response.text:
            raise RuntimeError('Gemini returned an empty response. Try again.')
        parsed = Verdict.model_validate_json(response.text)
    result = parsed.model_dump()
    result['confidence'] = max(0, min(100, result['confidence']))
    return investigation, result, model
