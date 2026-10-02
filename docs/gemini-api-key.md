# Gemini API Key Setup for a Free-Tier Test

Natural-language **Prompt to CAD** generation needs a Google Gemini API key and internet access. Deterministic examples and batch exports work without either. You can test prompt generation using Google's API free tier alongside a local Solid Edge installation; see the [Community Edition guide](solid-edge-community-edition.md) for Siemens' free personal-use option and its compatibility boundaries.

## 1. Get a Gemini API key

1. Sign in with your Google account at the official [Google AI Studio API Keys page](https://aistudio.google.com/apikey) and accept the service terms if prompted.
2. Select a project on the **Free** tier. AI Studio creates a default project and key for new users. If you already have Google Cloud projects, use **Projects → Import projects** to make your chosen project available in AI Studio.
3. Copy the default key, or choose **Create API key** for that project. Keep it private; do not put it in Git, screenshots, or shared logs.

For a free-tier test, use a project without paid billing enabled. A key inherits its project's billing and quota settings; creating another key does not create a separate free allowance. Google's [key setup](https://ai.google.dev/gemini-api/docs/api-key) and [billing guide](https://ai.google.dev/gemini-api/docs/billing) explain project setup and paid upgrades.

## 2. Run a first prompt in CAD Copilot

1. Follow [Getting Started](getting-started.md), including the optional `engine[dev,gemini]` dependency installation. Confirm the deterministic **Example (Spur Gear)** run works with your Solid Edge installation first.
2. Switch to **Prompt to CAD** in the Generate workspace. Paste your key into **Google Gemini API Configuration** and click **Save**. This saves it for the current app session only; it is not written to disk. Enter it again after restarting the app.
3. Choose an output folder and enter a simple prompt, such as:

   ```text
   Create a rectangular block 50 mm long, 40 mm wide, and 10 mm high.
   ```

4. Check **Keep part open in Solid Edge** if you want to inspect the saved part immediately after generation. Leave it unchecked to close the part after export.
5. Click **Run CAD Generation**. Check the generated `.par`, STEP, STL, and `run_manifest.json`, and confirm the saved part's dimensions. The preview image is best effort; a preview warning does not invalidate successful CAD exports.

## 3. Free-tier limits and troubleshooting

As checked on **2 October 2026**, CAD Copilot defaults to `gemini-3.5-flash-lite`, whose standard input and output are listed as free of charge on Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing). Free access has model and project quotas and is subject to Google's eligibility requirements; check the current pricing and your project's [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) before testing.

- **Quota or rate limit reached:** Check your project's limits in AI Studio and retry after the relevant limit resets. Generating a new key for the same project does not reset its quota.
- **Key rejected:** Check that you copied the full key for the intended project. For an old blocked or unrestricted key, create a fresh key in AI Studio following Google's key setup guide.
- **Provider unavailable:** Confirm the Gemini dependencies are installed in the repository's `.venv`, a session key is saved, and your network can reach Google.

Use generic test prompts. Google's [unpaid-service terms](https://ai.google.dev/gemini-api/terms#unpaid-services) allow prompts and responses to be used for product improvement and possible human review, so avoid confidential or personal information. CAD Copilot sends the prompt text; native CAD files and exports remain local.
