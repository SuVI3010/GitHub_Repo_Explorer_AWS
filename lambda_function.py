import json
import os
import base64
import urllib.request
import urllib.error
import boto3
import uuid
import textwrap
from datetime import datetime

# --- AWS Config ---
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
BEDROCK_MODEL_ID = os.environ.get('BEDROCK_MODEL_ID', 'meta.llama3-8b-instruct-v1:0')

bedrock_client = boto3.client(service_name='bedrock-runtime')


# --- ENTRYPOINT ---
def lambda_handler(event, context):
    """Router for all API Gateway calls."""
    try:
        path = event.get('rawPath') or event.get('path')
        body = json.loads(event.get('body', '{}'))

        if path == '/observe':
            repo_url = body.get('repo_url')
            response_body = observe_repository(repo_url)

        elif path == '/get_file_content':
            repo_name = body.get('repo_name')
            file_path = body.get('file_path')
            response_body = get_github_data(repo_name, "contents", file_path)

        elif path == '/act':
            task = body.get('task')
            data = body.get('data')
            response_body = execute_action(task, data)

        elif path == '/chat':
            readme = body.get('readme_content')
            history = body.get('chat_history')
            question = body.get('question')
            response_body = chat_with_repo(readme, history, question)

        else:
            return create_response(404, {'error': f'Invalid path: {path}'})

        return create_response(200, response_body)

    except Exception as e:
        print(f"AGENT ERROR: {e}")
        return create_response(500, {'error': f'Internal server error: {str(e)}'})


# --- [OBSERVE] ---
def observe_repository(repo_url):
    """Collects repo metadata, README, file tree, and language info."""
    print(f"OBSERVE: Mission for {repo_url}")
    repo_name = '/'.join(repo_url.split("github.com/")[-1].split('/')[:2])
    observed_data = {}

    observed_data['readme'] = get_github_data(repo_name, "readme")
    main_branch_sha = get_github_data(repo_name, "main_branch_sha")

    if main_branch_sha:
        observed_data['file_tree'] = get_github_data(repo_name, "tree", main_branch_sha)

    observed_data['commits'] = get_github_data(repo_name, "commits")
    observed_data['languages'] = get_github_data(repo_name, "languages")
    observed_data['package_json'] = get_github_data(repo_name, "contents", "package.json")
    observed_data['requirements_txt'] = get_github_data(repo_name, "contents", "requirements.txt")

    observed_data['repo_name'] = repo_name
    print("OBSERVE: Data collection complete.")
    return observed_data


# --- [ACT] ---
def execute_action(task, data):
    """Calls Bedrock to execute one repo-specific task."""
    print(f"ACT: Asking Bedrock to perform task: {task}")
    prompt = ""

    def summarize_if_large(text, label="input", limit=6000):
        """If input is large, summarize it."""
        if text and len(text) > limit:
            print(f"⚠️ {label} too large ({len(text)} chars) — summarizing before task...")
            summary_prompt = f"""
            <|begin_of_text|><|start_header_id|>system<|end_header_id|>
            You are a summarizer. Condense this text into a clear, short summary under 500 words.
            <|eot_id|><|start_header_id|>user<|end_header_id|>
            {text[:limit*2]}
            <|eot_id|><|start_header_id|>assistant<|end_header_id|>
            """
            try:
                return call_bedrock(summary_prompt)
            except Exception as e:
                print(f"⚠️ Summarization failed: {e}")
                return text[:limit] + "\n[Truncated due to size]"
        return text

    # --- Summarize Repo Purpose ---
    if task == "Summarize Repo Purpose":
        readme_text = summarize_if_large(data if isinstance(data, str) else json.dumps(data), "README.md")
        prompt = f"""
        <|begin_of_text|><|start_header_id|>user<|end_header_id|>
        Please provide a concise, one-paragraph summary of this project's README:
        ---
        {readme_text}
        ---
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """

    # --- Identify Tech Stack & Dependencies ---
    elif task == "Identify Tech Stack & Dependencies":
        languages = data.get('languages', {})
        pkg_json = summarize_if_large(str(data.get('package_json', '')), "package.json")
        req_txt = summarize_if_large(str(data.get('requirements_txt', '')), "requirements.txt")
        prompt = f"""
        <|begin_of_text|><|start_header_id|>user<|end_header_id|>
        Identify this project's primary technologies and dependencies.
        - Languages: {json.dumps(languages)}
        - package.json: {pkg_json}
        - requirements.txt: {req_txt}
        Return a bullet list of major frameworks and libraries.
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """

    # --- Analyze Activity Trends ---
    # --- Analyze Activity Trends ---
    elif task == "Analyze Activity Trends":
        commits = data.get('commits', [])
        if not isinstance(commits, list):
            commits = [commits]
        commit_dates = [
            c.get('commit', {}).get('author', {}).get('date', 'unknown')
            for c in commits if isinstance(c, dict)
        ]
        if not commit_dates:
            return {"result": "No commit data found — unable to analyze activity."}
        prompt = f"""
        <|begin_of_text|><|start_header_id|>user<|end_header_id|>
        Based on these commit timestamps, describe whether this repository is
        very active, moderately active, or stale. Provide a single, concise assessment.
        Commit Dates:
        {', '.join(commit_dates[:100])}
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """

    # --- Find Key Contributors ---
    elif task == "Find Key Contributors":
        commits = data.get('commits', [])
        if not isinstance(commits, list):
            commits = [commits]
        authors = [
            c.get('author', {}).get('login', 'unknown')
            for c in commits if isinstance(c, dict)
        ]
        if not authors:
            return {"result": "No contributor data available in commit history."}
        prompt = f"""
        <|begin_of_text|><|start_header_id|>user<|end_header_id|>
        From this list of commit authors, identify the top 3–5 most frequent contributors,
        count their commits, and summarize their roles if identifiable.
        Authors:
        {', '.join(authors[:200])}
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """


    # --- Explain File (fixed logic) ---
    elif task == "Explain File":
        readme_context = summarize_if_large(data.get("readme_content", ""), "README context")
        file_content = data.get("file_content", "").strip()
        if not file_content:
            return {"error": "No file content available for explanation."}

        prompt = f"""
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>
        You are an expert software engineer. Use the README only as context for the project's purpose,
        and focus on explaining the provided file's code logic and role within the project.
        Avoid restating README information.
        <|eot_id|><|start_header_id|>user<|end_header_id|>
        <README_CONTEXT>
        {readme_context[:6000]}
        </README_CONTEXT>

        <FILE_CONTENT>
        {file_content[:16000]}
        </FILE_CONTENT>

        Explain clearly:
        • What this file does and how it fits into the repo  
        • Its main functions or classes  
        • Any dependencies or integrations  
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """

    else:
        return {'error': f"Unknown action task: {task}"}

    try:
        result = call_bedrock(prompt)
        return {'result': result.strip()}
    except Exception as e:
        print(f"ACT ERROR: {e}")
        return {'error': f"Internal server error: {str(e)}"}


# --- [CHAT] ---
def chat_with_repo(readme_content, chat_history, new_question):
    """Conversational Q&A with context summarization."""
    print(f"CHAT: Answering question: {new_question}")
    readme_content = readme_content or "No README.md content available."
    chat_history = chat_history or []

    history_text = ""
    for turn in chat_history:
        history_text += f"<|start_header_id|>user<|end_header_id|>\n{turn.get('user', '')}<|eot_id|>"
        history_text += f"<|start_header_id|>assistant<|end_header_id|>\n{turn.get('agent', '')}<|eot_id|>"

    full_prompt = f"""
    <|begin_of_text|><|start_header_id|>system<|end_header_id|>
    You are a GitHub project expert assistant. Answer based only on the README and context.
    If you don't know, say "I do not have that information in the README."
    <|eot_id|>
    <README_CONTEXT>
    {readme_content[:40000]}
    </README_CONTEXT>
    {history_text}
    <|start_header_id|>user<|end_header_id|>
    {new_question}
    <|eot_id|><|start_header_id|>assistant<|end_header_id|>
    """

    context_size = len(full_prompt)
    print(f"CHAT CONTEXT SIZE: {context_size} chars")

    # Summarize if too long
    if context_size > 7000:
        print("⚠️ Chat context too long — summarizing history...")
        summarize_prompt = f"""
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>
        Condense this discussion into a short summary keeping all technical meaning.
        <|eot_id|><|start_header_id|>user<|end_header_id|>
        {history_text}
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """
        summary_text = call_bedrock(summarize_prompt)
        condensed_prompt = f"""
        <|begin_of_text|><|start_header_id|>system<|end_header_id|>
        Use this conversation summary and the README to answer:
        <SUMMARY>{summary_text}</SUMMARY>
        <README_CONTEXT>{readme_content[:40000]}</README_CONTEXT>
        <|start_header_id|>user<|end_header_id|>
        {new_question}
        <|eot_id|><|start_header_id|>assistant<|end_header_id|>
        """
        return {"result": call_bedrock(condensed_prompt)}

    return {"result": call_bedrock(full_prompt)}


# --- [HELPERS] ---
def call_bedrock(prompt):
    """Generic Bedrock model invocation."""
    body = json.dumps({
        "prompt": prompt,
        "max_gen_len": 1024,
        "temperature": 0.1,
        "top_p": 0.9,
    })
    response = bedrock_client.invoke_model(
        body=body,
        modelId=BEDROCK_MODEL_ID,
        contentType="application/json",
        accept="application/json"
    )
    response_body = json.loads(response['body'].read())
    return response_body.get('generation', '').strip()


def get_github_data(repo_name, endpoint_type, path=None, max_pages=5):
    """Fetches data from GitHub API (robust, paginated, guaranteed decoded content)."""
    base_url = f"https://api.github.com/repos/{repo_name}"

    # Build URL map for supported endpoints
    url_map = {
        "readme": f"{base_url}/readme",
        "main_branch_sha": f"{base_url}/branches/master",
        "tree": f"{base_url}/git/trees/{path}?recursive=1" if path else None,
        "contents": f"{base_url}/contents/{path}" if path else None,
        "commits": f"{base_url}/commits",
        "languages": f"{base_url}/languages"
    }
    url = url_map.get(endpoint_type)
    if not url:
        return None

    # Common headers (now properly defined)
    req_headers = {
        'Authorization': f'token {GITHUB_TOKEN}' if GITHUB_TOKEN else '',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'AWS-Lambda-AgentCockpit'
    }

    try:
        # --- Commits (special paginated handling) ---
        if endpoint_type == "commits":
            all_commits = []
            for page in range(1, max_pages + 1):
                paged_url = f"{base_url}/commits?per_page=100&page={page}"
                req = urllib.request.Request(paged_url, headers={k: v for k, v in req_headers.items() if v})
                try:
                    with urllib.request.urlopen(req) as resp:
                        batch = json.loads(resp.read().decode("utf-8"))
                        if not batch:
                            break
                        all_commits.extend(batch)
                except urllib.error.HTTPError as e:
                    print(f"Pagination stopped at page {page}: {e}")
                    break
            print(f"Fetched {len(all_commits)} commits from GitHub.")
            return all_commits

        # --- Standard single-call endpoints ---
        req = urllib.request.Request(url, headers={k: v for k, v in req_headers.items() if v})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))

            # README / file content
            if endpoint_type in ["readme", "contents"]:
                if "content" in data:
                    return base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
                if "download_url" in data:
                    with urllib.request.urlopen(data["download_url"]) as raw:
                        return raw.read().decode("utf-8", errors="ignore")
                return f"[Error: No content found for {path}]"

            # Repo file tree
            if endpoint_type == "tree":
                return data.get("tree", [])

            # Languages (ensure dict)
            if endpoint_type == "languages":
                return data if isinstance(data, dict) else {}

            # Branch SHA
            if endpoint_type == "main_branch_sha":
                return data.get("commit", {}).get("sha")

            # Default fallback
            return data

    except urllib.error.HTTPError as e:
        print(f"GitHub API Error: {e.code} for {url}")
        return None
    except Exception as e:
        print(f"Unexpected GitHub API error: {e}")
        return None


def create_response(statusCode, body):
    """Standard API Gateway response."""
    return {
        'statusCode': statusCode,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
        },
        'body': json.dumps(body)
    }
