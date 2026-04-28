import os
import json
import traceback
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

class DocumentAnalyzer:
    def __init__(self):
        # Using the stable 2025/2026 endpoints confirmed for your environment
        self.flash_model = "gemini-2.5-flash"
        self.pro_model = "gemini-2.5-pro"
        
        # Initialize the client using the API key from environment
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("❌ ERROR: GEMINI_API_KEY not found in environment variables.")
        
        self.client = genai.Client(api_key=api_key)

    def _clean_json_response(self, text: str) -> str:
        """Removes markdown backticks (```json ... ```) to prevent parsing errors."""
        text = text.strip()
        if "```" in text:
            # Extract content between the first and last triple backticks
            parts = text.split("```")
            for part in parts:
                if "{" in part and "}" in part:
                    text = part
                    if text.startswith("json"):
                        text = text[4:]
                    break
        return text.strip()

    def extract_document_data(self, file_bytes: bytes, file_ext: str, doc_type: str) -> dict:
        """Step 1: Use Flash to convert PDF/Image into structured JSON data."""
        print(f"--- 🔍 Extracting {doc_type} Data (using {self.flash_model}) ---")
        
        mime_type = "application/pdf" if file_ext.lower() == "pdf" else f"image/{file_ext.lower()}"
        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        prompt = f"""
        Extract the following as a structured JSON object from this {doc_type}:
        - Vendor Name and Email
        - Itemized list (Quantity, Description, Unit Price)
        - Total Amount
        - Bank Details (Bank Name, Account Number, IFSC)
        - Tax ID (GSTIN/VAT)

        Return ONLY the JSON object.
        """

        try:
            response = self.client.models.generate_content(
                model=self.flash_model,
                contents=[prompt, file_part],
                config=types.GenerateContentConfig(temperature=0.0)
            )
            
            clean_text = self._clean_json_response(response.text)
            return json.loads(clean_text)
        except Exception as e:
            print(f"⚠️ Extraction Warning: {e}")
            return {"error": "extraction_failed", "raw": response.text if response else "No response"}

    def analyze_documents(self, inv_bytes: bytes, inv_ext: str, po_bytes: bytes, po_ext: str) -> dict:
        """Step 2: Cross-reference Invoice vs PO using Pro (CoT Reasoning)."""
        
        # 1. Extract data from both documents
        invoice_data = self.extract_document_data(inv_bytes, inv_ext, "Invoice")
        po_data = self.extract_document_data(po_bytes, po_ext, "Purchase Order")

        print(f"--- 🧠 Performing CoT Reasoning (using {self.pro_model}) ---")
        
        reasoning_prompt = f"""
        You are an SME Financial Auditor (UN SDG 9.3). 
        Analyze this Invoice against the Purchase Order (PO). 
        
        CRITICAL TASKS:
        1. BEC Fraud Check: Do the Bank Account Numbers or Email Domains differ?
        2. Price Variance: Is the Unit Price more than 3% higher than the PO?
        3. Identity: Are the Vendor Names and Tax IDs consistent?

        INVOICE DATA: {json.dumps(invoice_data)}
        PO DATA: {json.dumps(po_data)}

        Provide your findings in this JSON format:
        {{
          "risk_level": "Low" | "Medium" | "High Risk",
          "summary": "Short 1-sentence summary",
          "reasoning": "Bullet points of your logic",
          "flags": ["list", "of", "issues"]
        }}
        """

        try:
            response = self.client.models.generate_content(
                model=self.pro_model,
                contents=[reasoning_prompt],
                config=types.GenerateContentConfig(temperature=0.1)
            )

            clean_text = self._clean_json_response(response.text)
            # Find the first { and last } to be safe
            start = clean_text.find('{')
            end = clean_text.rfind('}') + 1
            
            result = json.loads(clean_text[start:end])
            result['full_analysis_log'] = response.text
            return result
            
        except Exception as e:
            traceback.print_exc()
            return {
                "risk_level": "Unknown Error",
                "summary": f"System crash: {str(e)}",
                "reasoning": "The AI reasoning engine failed to parse correctly."
            }
