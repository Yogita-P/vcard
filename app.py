import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import io
import json
import re
import smtplib
from google import genai
from PIL import Image
import requests
import streamlit as st

# ==========================================
# PAGE SETUP & CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Automated Lead Dispatcher", page_icon="🎴", layout="centered"
)

st.title("🎴 Automated Lead Dispatcher")
st.write(
    "Upload a business card to automatically extract details and immediately dispatch Email & WhatsApp outreach."
)

# Load Credentials from Streamlit Secrets (.streamlit/secrets.toml)
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = st.secrets.get("OPENROUTER_API_KEY", "")
SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "")
SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "")
META_ACCESS_TOKEN = st.secrets.get("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = st.secrets.get("META_PHONE_NUMBER_ID", "")

COMPANY_NAME = "TROVAI TECHNOLOGY"
COMPANY_INFO = "TROVAI TECHNOLOGY provides cutting-edge AI and automation solutions. Reply to learn more!"


# ==========================================
# PHONE NUMBER FORMATTING (+91 INDIA DEFAULT)
# ==========================================
def format_phone_number(phone_str: str) -> str:
    """Ensures phone number has a country code. Defaults to +91 (India) if missing."""
    if not phone_str:
        return ""

    # Remove non-digit characters except leading +
    cleaned = re.sub(r"[^\d+]", "", str(phone_str).strip())

    # If phone number doesn't start with '+', attach +91 (India)
    if not cleaned.startswith("+"):
        # Remove leading zero if present (e.g. 09876543210 -> 9876543210)
        if cleaned.startswith("0"):
            cleaned = cleaned[1:]
        cleaned = f"+91{cleaned}"

    return cleaned


# ==========================================
# EXTRACTION LOGIC (GEMINI PRIMARY -> OPENROUTER FALLBACK)
# ==========================================
def extract_with_gemini(image: Image.Image) -> dict:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = """
        Extract contact information from this business card.
        
        Rules:
        - "name": Full name.
        - "email": Primary email address.
        - "phone": Exactly ONE mobile number. Strip all spaces, hyphens, and parentheses. If multiple exist, pick the primary mobile number. Do not concatenate numbers.

        Return ONLY a raw JSON object: {"name": "...", "email": "...", "phone": "..."}
        """
    response = client.models.generate_content(
        model="gemini-3.8-flash", contents=[image, prompt]
    )
    raw_text = re.sub(r"```json\s*|\s*```", "", response.text.strip())
    return json.loads(raw_text)


def extract_with_openrouter(image: Image.Image) -> dict:
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

    prompt = """
        Extract contact information from this business card.
        
        Rules:
        - "name": Full name.
        - "email": Primary email address.
        - "phone": Exactly ONE mobile number. Strip all spaces, hyphens, and parentheses. If multiple exist, pick the primary mobile number. Do not concatenate numbers.

        Return ONLY a raw JSON object: {"name": "...", "email": "...", "phone": "..."}
        """
    url = "https://openrouter.ai/api/v1/chat/completions".strip()
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openrouter/free",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        },
                    },
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    res_data = response.json()

    if response.status_code != 200:
        raise Exception(
            res_data.get("error", {}).get("message", "OpenRouter Error")
        )

    content = re.sub(
        r"```json\s*|\s*```",
        "",
        res_data["choices"][0]["message"]["content"].strip(),
    )
    return json.loads(content)


def extract_info_from_card(image: Image.Image) -> dict:
    """Primary Gemini Extraction with OpenRouter Fallback."""
    if GEMINI_API_KEY:
        try:
            return extract_with_gemini(image)
        except Exception as e:
            st.warning(f"⚠️ Gemini API failed ({e}). Trying OpenRouter...")

    if OPENROUTER_API_KEY:
        try:
            return extract_with_openrouter(image)
        except Exception as e:
            st.error(f"❌ OpenRouter API also failed: {e}")

    return {"name": None, "email": None, "phone": None}


# ==========================================
# DISPATCH MESSAGING FUNCTIONS
# ==========================================
def dispatch_email(recipient_email: str, recipient_name: str) -> bool:
    if not recipient_email or not SENDER_EMAIL or not SENDER_APP_PASSWORD:
        return False

    subject = f"Great connecting with you, {recipient_name or 'there'}! - {COMPANY_NAME}"
    body = f"Hi {recipient_name or 'there'},\n\nThanks for sharing your contact card!\n\n{COMPANY_INFO}\n\nBest,\n{COMPANY_NAME}"
    msg = MIMEMultipart()
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
    return True

def dispatch_meta_whatsapp(
    recipient_phone: str, message_text: str
) -> tuple[bool, str]:
    if not recipient_phone or not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        return (
            False,
            "Missing recipient phone number, META_ACCESS_TOKEN, or META_PHONE_NUMBER_ID.",
        )

    clean_phone = (
        recipient_phone.replace("+", "").replace(" ", "").replace("-", "")
    )
    url = f"https://graph.facebook.com/v20.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_phone,
        "type": "text",
        "text": {"body": message_text},
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        res_data = response.json()

        if response.status_code == 200:
            message_id = res_data.get("messages", [{}])[0].get("id", "unknown")
            print(f"WhatsApp request accepted for {clean_phone}. Response: {res_data}")
            return True, f"Accepted by Meta. Message ID: {message_id}"
        else:
            error_details = res_data.get("error", {})
            error_msg = error_details.get("message", "Unknown Meta API error")
            error_code = error_details.get("code", "No Code")
            return False, f"Code {error_code}: {error_msg}"

    except Exception as e:
        return False, f"Network/Request Exception: {str(e)}"

# def dispatch_meta_whatsapp(recipient_phone: str, message_text: str) -> bool:
#     if not recipient_phone or not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
#         return False

#     clean_phone = (
#         recipient_phone.replace("+", "").replace(" ", "").replace("-", "")
#     )
#     url = f"https://graph.facebook.com/v25.0/{META_PHONE_NUMBER_ID}/messages"
#     headers = {
#         "Authorization": f"Bearer {META_ACCESS_TOKEN}",
#         "Content-Type": "application/json",
#     }
#     payload = {
#         "messaging_product": "whatsapp",
#         "to": clean_phone,
#         "type": "text",
#         "text": {"body": message_text},
#     }

#     try:
#             response = requests.post(url, headers=headers, json=payload, timeout=15)
#             res_data = response.json()

#             if response.status_code == 200:
#                 print(f"WhatsApp sent successfully to {clean_phone}. Response: {res_data}")
#                 return True, "Success"
#             else:
#                 # Extract Meta's precise error message
#                 error_details = res_data.get("error", {})
#                 error_msg = error_details.get("message", "Unknown Meta API error")
#                 error_code = error_details.get("code", "No Code")
#                 return False, f"Code {error_code}: {error_msg}"
                
#     except Exception as e:
#             return False, f"Network/Request Exception: {str(e)}"


# ==========================================
# MAIN APP WORKFLOW (AUTOMATED ATTEMPT)
# ==========================================
uploaded_file = st.file_uploader(
    "Upload Business Card Image", type=["jpg", "jpeg", "png"]
)

if uploaded_file:
    image = Image.open(uploaded_file)
    st.image(image, caption="Uploaded Card", use_container_width=True)

    # Automatically process card upon image upload
    if (
        "last_uploaded_name" not in st.session_state
        or st.session_state["last_uploaded_name"] != uploaded_file.name
    ):
        st.session_state["last_uploaded_name"] = uploaded_file.name

        with st.spinner(
            "⚡ Extracting details and automatically shooting outreach..."
        ):
            # 1. Extract Details
            extracted_data = extract_info_from_card(image)

            # Format Phone Number (Defaulting to +91 India if missing country code)
            formatted_phone = format_phone_number(
                extracted_data.get("phone") or ""
            )
            extracted_data["phone"] = formatted_phone

            st.session_state["extracted_data"] = extracted_data

            # 2. Immediate Automatic Dispatch
            email_val = extracted_data.get("email")
            phone_val = extracted_data.get("phone")
            name_val = extracted_data.get("name")

            # Dispatch Email
            email_status = False
            if email_val:
                try:
                    email_status = dispatch_email(email_val, name_val)
                except Exception as e:
                    st.error(f"Automatic Email failed: {e}")

            # Dispatch WhatsApp
            wa_status = False
            wa_detail = ""
            if phone_val:
                wa_msg = f"Hi {name_val or 'there'}, great connecting with you! {COMPANY_INFO}"
                try:
                    wa_status, wa_detail = dispatch_meta_whatsapp(phone_val, wa_msg)
                except Exception as e:
                    wa_detail = f"Automatic WhatsApp failed: {e}"
                    st.error(wa_detail)

            st.session_state["email_sent"] = email_status
            st.session_state["wa_sent"] = wa_status
            st.session_state["wa_detail"] = wa_detail

    # Show Immediate Delivery Results Summary
    st.markdown("---")
    st.subheader("📊 Instant Dispatch Status")

    email_ok = st.session_state.get("email_sent", False)
    wa_ok = st.session_state.get("wa_sent", False)
    wa_detail = st.session_state.get("wa_detail", "")

    col1, col2 = st.columns(2)
    with col1:
        if email_ok:
            st.success("✉️ Email sent successfully!")
        else:
            st.error("✉️ Email sending pending or failed.")

    with col2:
        if wa_ok:
            st.success("📱 WhatsApp request accepted by Meta.")
        else:
            st.error(f"📱 WhatsApp failed: {wa_detail or 'No recipient phone number found.'}")

    # FALLBACK EDIT FORM (For manual retry or adjustments)
    st.markdown("---")
    st.subheader("🛠️ Verify / Edit Details & Resend")

    data = st.session_state.get("extracted_data", {})
    edit_name = st.text_input("Name", value=data.get("name") or "")
    edit_email = st.text_input("Email", value=data.get("email") or "")
    edit_phone = st.text_input(
        "Phone Number (with Country Code)", value=data.get("phone") or ""
    )

    if st.button("🔄 Retry / Send Manual Outreach", type="primary"):
        # Re-format phone number manually edited by user
        final_phone = format_phone_number(edit_phone)

        # Retry Email
        if edit_email:
            try:
                if dispatch_email(edit_email, edit_name):
                    st.success("✉️ Manual Email Sent Successfully!")
            except Exception as e:
                st.error(f"Email failed: {e}")

        # Retry WhatsApp
        if final_phone:
            wa_msg = f"Hi {edit_name or 'there'}, great connecting with you! {COMPANY_INFO}"
            try:
                manual_wa_ok, manual_wa_detail = dispatch_meta_whatsapp(final_phone, wa_msg)
                if manual_wa_ok:
                    st.success("📱 WhatsApp request accepted by Meta.")
                else:
                    st.error(f"WhatsApp failed: {manual_wa_detail}")
            except Exception as e:
                st.error(f"WhatsApp failed: {e}")