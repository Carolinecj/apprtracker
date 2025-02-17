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

# --- CONFIGURE YOUR SETTINGS ---
rss_url = "https://www.drugs.com/feeds/new_drug_approvals.xml"
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
    for line in text.splitlines():
        if line.strip().startswith('-'):
            parts = line[1:].strip().split(": ", 1)
            if len(parts) == 2:
                key, value = parts
                data[key.strip()] = value.strip()
    return data

def extract_info_with_chatgpt(title, description):
    prompt = f"""
    Extract the following structured data from the text:
    - Drug or vaccine Name
    - Pharmaceutical Company
    - Publish Date
    - Indication
    
    Text:
    Title: {title}
    Description: {description}
    """
    
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "system", "content": "You are a helpful assistant that extracts structured information from text."},
                  {"role": "user", "content": prompt}]
    )
    
    return response.choices[0].message.content

# Get today's date and the date 30 days ago
today = datetime.datetime.now(datetime.timezone.utc)
week_ago = today - datetime.timedelta(days=7)

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
feed = feedparser.parse(rss_url)

new_entries = []
# Filter approvals from the last 7 days
#recent_approvals = []
for entry in feed.entries:
    approval_date = datetime.datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
    #if approval_date.replace(tzinfo=datetime.timezone.utc) >= last_month:
    if approval_date.replace(tzinfo=datetime.timezone.utc) >= week_ago:
        structured_data = extract_info_with_chatgpt(entry.title, entry.summary)
        data_dict = extract_info_from_text(structured_data)
    if data_dict:
            drug_name = data_dict.get("Drug Name", "Not Found")
            approval_date_str = approval_date.strftime("%Y-%m-%d")
            unique_id = f"{drug_name}_{approval_date_str}"  # Unique identifier
            
            # Avoid duplicates before adding
            if unique_id not in existing_identifiers:
                new_entries.append([
                    entry.title,
                    approval_date_str,
                    drug_name,
                    data_dict.get("Pharmaceutical Company", "Not Found"),
                    data_dict.get("Indication", "Not Found"),
                    entry.summary,
                    entry.link
                ])
                existing_identifiers.add(unique_id)
      #  if data_dict:
      #      recent_approvals.append([
      #      entry.title,
      #      approval_date.strftime("%Y-%m-%d"),
      #       data_dict.get("Drug Name", "Not Found"),
           #     data_dict.get("Pharmaceutical Company", "Not Found"),
        #        data_dict.get("Indication", "Not Found"),
         #       entry.summary,
          #      entry.link
            #])

# --- WRITE TO GOOGLE SHEETS ---
if new_entries:
    sheet.append_rows(new_entries)  # Append all data at once
    print(f"✅ Successfully written {len(new_entries)} entries to Google Sheets")
else:
    print("No new FDA approvals in the last day.")
