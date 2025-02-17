import requests
import datetime
import pandas as pd
import feedparser
import openai
import os
import json 
from google.oauth2.service_account import Credentials
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dateutil import parser
from dateutil.tz import gettz

# --- CONFIGURE YOUR SETTINGS ---
rss_urls = [
    "https://www.drugs.com/feeds/new_drug_approvals.xml",  # Drugs.com feed
    "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"  # FDA feed
]
openai.api_key = os.getenv('OPENAI_API_KEY2')

# Load credentials from GitHub Secret
credentials_json = os.getenv("GS_CREDENTIALS")

# Authenticate with Google Sheets API
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"] 

if credentials_json:
    credentials_dict = json.loads(credentials_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
else:
    raise ValueError("Google Sheets credentials not found")


client = gspread.authorize(creds)

# Open your Google Sheet (Make sure you've shared it with your service account email)
spreadsheet = client.open("FDA Approvals tracker")  # Change to your Google Sheet name
sheet = spreadsheet.sheet1  # Use the first sheet

def extract_info_from_text(text):
    data = {}
    # Strip the text to remove unnecessary leading/trailing spaces
    text = text.strip()

    try:
        # Parse the text as JSON, if possible
        parsed_data = json.loads(text)
        
        # Ensure all expected keys are present, if missing, insert empty values or placeholders
        expected_keys = ["Drug Name", "Vaccine Name", "Pharmaceutical Company", "Publish Date", "Indication"]
        for key in expected_keys:
            if key not in parsed_data:
                parsed_data[key] = "Not Provided"  # Or another suitable default value

        return parsed_data

    except json.JSONDecodeError:
        # In case parsing fails, return an empty dictionary and print a warning
        print(f" WARNING: Failed to decode JSON: {text}")
        return {}



def extract_info_with_chatgpt(title, description):
    prompt = f"""
    Extract the following structured data from the text below. If any field is missing or unclear, do your best to infer it. Return the response as structured JSON.

    **Fields to extract:**
    - "Drug Name": The name of the drug or vaccine (if applicable). If not identified, return 'N/A' (no additional text or explanation)
    - "Vaccine Name": The name of the vaccine (if applicable). If not identified, return 'N/A' (no additional text or explanation)
    - "Pharmaceutical Company": The company that developed or manufactures the drug/vaccine.
    - "Publish Date": The approval announcement date.
    - "Indication": The medical condition or purpose the drug/vaccine is approved for.

    **Text to analyze:**
    Title: {title}
    Description: {description}

    **Output format (JSON):**
    {{
        "Drug Name": "ExampleDrug",
        "Vaccine Name": "ExampleVaccine",
        "Pharmaceutical Company": "Example Pharma Inc.",
        "Publish Date": "YYYY-MM-DD",
        "Indication": "Used to treat XYZ condition."
    }}

    Extract carefully and return **only** valid JSON.
    """
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a helpful assistant that extracts structured information from text."},
            {"role": "user", "content": prompt}
        ]
    )
    
    return response.choices[0].message.content  # Correct indentation

# Define a mapping for common time zone abbreviations
tzinfos = {
    "EST": gettz("America/New_York"),
    "PST": gettz("America/Los_Angeles"),
    "CST": gettz("America/Chicago"),
    "MST": gettz("America/Denver"),
    "EDT": gettz("America/New_York"),
    "PDT": gettz("America/Los_Angeles"),
    "CDT": gettz("America/Chicago"),
    "MDT": gettz("America/Denver"),
}

def parse_approval_date(date_str):
    try:
        # Use dateutil.parser to handle both time zone abbreviations and offsets
        return parser.parse(date_str,tzinfos=tzinfos)
    except Exception as e:
        print(f"Error parsing date: {date_str}, {e}")
        return None
# Get today's date and the date 30 days ago
today = datetime.datetime.now(datetime.timezone.utc)
week_ago = today - datetime.timedelta(days=30)

#last_month = today - datetime.timedelta(days=30)
#yesterday = today - datetime.timedelta(days=1)

#Fetch existing entries from google sheets
existing_records= sheet.get_all_values() # get all existing records
existing_identifiers= set()

# Assuming column structure: [Title, Approval Date, Drug Name, Company, Indication, Summary, Link]
for row in existing_records[1:]:  # Skip header
    if len(row) > 2:  # Ensure we have enough columns
        existing_identifiers.add(f"{row[2]}_{row[1]}")  # Drug Name + Approval Date

# --- GET FDA APPROVALS FROM rss feeds ---
def process_rss_feed(rss_url, existing_identifiers):
    feed = feedparser.parse(rss_url)
    recent_approvals = []

    # List of relevant approval-related keywords
    relevant_keywords = ['approval', 'approved', 'approves']
    # List of denial-related keywords
    denial_keywords = ['denied', 'not approved', 'not authorized', 'not eligible', 'rejected']

    for entry in feed.entries:
        # Skip articles with denial-related keywords in the title (case-insensitive)
        if any(keyword in entry.title.lower() for keyword in denial_keywords):
            continue  # Skip this article

        # Check if any relevant approval keyword is in the title (case-insensitive)
        if not any(keyword in entry.title.lower() for keyword in relevant_keywords):
            continue  # Skip irrelevant articles

        # Convert published date to datetime
        try:
            approval_date = parser.parse(entry.published)
        except Exception as e:
            print(f"Error parsing date: {entry.published}, error: {e}")
            continue  # Skip this entry if date parsing fails

        # Filter by approval date (last 30 days or any other time range as needed)
        if approval_date.replace(tzinfo=datetime.timezone.utc) >= week_ago:
            # Send title and summary to ChatGPT for structured data extraction
            structured_data = extract_info_with_chatgpt(entry.title, entry.summary)
            print("DEBUG: Raw structured data from ChatGPT:")
            print(structured_data)
            
            # Parse the extracted data into a dictionary
            data_dict = extract_info_from_text(structured_data)

            print("DEBUG: Parsed data dictionary:")
            print(json.dumps(data_dict, indent=2))
            
            # If drug name or pharmaceutical company is "N/A", skip saving this entry
            drug_name = data_dict.get("Drug Name", "N/A")
            vaccine_name = data_dict.get("Vaccine Name", "N/A")

            # If Drug Name is "N/A" but Vaccine Name has a valid value, use Vaccine Name
            if drug_name == "N/A" and vaccine_name != "N/A":
                drug_name = vaccine_name

            # Skip if both Drug Name and Vaccine Name are "N/A"
            if drug_name == "N/A":
                print(f"DEBUG: Skipping entry - Drug Name: {data_dict.get('Drug Name')}, Vaccine Name: {data_dict.get('Vaccine Name')}")
                continue

            pharmaceutical_company = data_dict.get("Pharmaceutical Company", "N/A")

                       
            # Basic check for approval/denial (example keywords)
            if 'approved' in entry.title.lower():
                approval_status = 'Approved'
            elif 'denied' in entry.title.lower():
                approval_status = 'Denied'
            else:
                approval_status = 'Unclear'
            
            # Create a unique identifier based on drug name and approval date
            approval_date_str = approval_date.strftime("%Y-%m-%d")
            unique_id = f"{drug_name}_{approval_date_str}"

            # Avoid duplicates before adding to recent_approvals
            if unique_id not in existing_identifiers:
                recent_approvals.append({
                    "Title": entry.title,
                    "Approval Date": approval_date_str,
                    "Drug Name": drug_name,
                    "Pharmaceutical Company": pharmaceutical_company,
                    "Indication": data_dict.get("Indication", "Not Found"),
                    "Approval Status": approval_status,
                    "Summary": entry.summary,
                    "Link": entry.link
                })
                existing_identifiers.add(unique_id)  # Add unique_id to track duplicates

    return recent_approvals

all_recent_approvals = []

# Process each RSS feed
for rss_url in rss_urls:
    approvals = process_rss_feed(rss_url,existing_identifiers)
    all_recent_approvals.extend(approvals)

# --- WRITE TO GOOGLE SHEETS ---
if all_recent_approvals:
    sheet.append_rows([list(approval.values()) for approval in all_recent_approvals])  # Write to Google Sheets
    print(f"✅ Successfully written {len(all_recent_approvals)} entries to Google Sheets")
else:
    print("No new FDA approvals in the last month.")
