(function () {
  if (!window._workshopConfig) {
    alert("CRITICAL ERROR: config.js missing.");
    return;
  }

  const poolData = {
    UserPoolId: window._workshopConfig.cognito.userPoolId,
    ClientId: window._workshopConfig.cognito.userPoolClientId,
  };
  const userPool = new AmazonCognitoIdentity.CognitoUserPool(poolData);
  let cognitoUser, idToken;

  // Persistent chat memory across sessions
  let observedData = { repo_name: null, readme: null, file_tree: [] };
  let chatHistory = JSON.parse(localStorage.getItem("chatHistory") || "[]");

  // DOM Elements
  const loginContainer = document.getElementById('login-container');
  const newPasswordContainer = document.getElementById('new-password-container');
  const appContainer = document.getElementById('app-container');
  const missionForm = document.getElementById('mission-form');
  const loginForm = document.getElementById('login-form');
  const newPasswordForm = document.getElementById('new-password-form');
  const statusLog = document.getElementById('status-log');
  const finalReport = document.getElementById('final-report');
  const appError = document.getElementById('app-error');
  const fileListContainer = document.getElementById('file-list');
  const analyzeButton = document.getElementById('analyze-button');
  const chatForm = document.getElementById('chat-form');
  const chatInput = document.getElementById('chat-input');
  const chatWindow = document.getElementById('chat-window');
  const logoutButton = document.getElementById('logout-button');

  // --- Utility Logging ---
  function logStatus(msg) {
    console.log(msg);
    statusLog.textContent = `AGENT: ${msg}\n${statusLog.textContent}`;
  }

  // --- API Helper ---
  async function callApi(path, body) {
    const API_URL = window._workshopConfig.api.invokeUrl.replace(/\/$/, '');
    const res = await fetch(API_URL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': idToken },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || `Error ${res.status}`);
    }
    return res.json();
  }

  // --- Mission Workflow ---
  missionForm.addEventListener('submit', async e => {
    e.preventDefault();
    appError.textContent = '';
    statusLog.textContent = '';
    finalReport.innerHTML = '';
    fileListContainer.innerHTML = '<p>Loading repository...</p>';
    analyzeButton.disabled = true;
    analyzeButton.textContent = 'Analyzing...';

    try {
      const repo_url = document.getElementById('repo-url').value;
      logStatus("Observing repository...");
      const data = await callApi('/observe', { repo_url });
      observedData = { ...data };
      logStatus(`Observation complete. Found ${data.file_tree.length} files.`);
      renderFileList(data.file_tree);

      const tasks = [];
      if (document.getElementById('task-summary')?.checked)
        tasks.push("Summarize Repo Purpose");
      if (document.getElementById('task-tech')?.checked)
        tasks.push("Identify Tech Stack & Dependencies");
      if (document.getElementById('task-activity')?.checked)
        tasks.push("Analyze Activity Trends");
      if (document.getElementById('task-contrib')?.checked)
        tasks.push("Find Key Contributors");

      for (const t of tasks) {
        logStatus(`Executing task: ${t}`);
        const result = await callApi('/act', { task: t, data });
        const section = document.createElement('div');
        section.innerHTML = `<h4>${t}</h4><pre>${result.result || '[No output received]'}</pre>`;
        finalReport.appendChild(section);
      }

    } catch (err) {
      console.error(err);
      appError.textContent = err.message;
      logStatus(err.message);
    } finally {
      analyzeButton.disabled = false;
      analyzeButton.textContent = '🚀 Analyze Repo';
    }
  });

  // --- Render File List ---
  function renderFileList(tree) {
    const list = document.createElement('ul');
    const files = (tree || [])
      .filter(f => f.path && (f.path.endsWith('.py') || f.path.endsWith('.js') || !f.path.includes('/')))
      .slice(0, 50);

    files.forEach(f => {
      const li = document.createElement('li');
      const link = document.createElement('a');
      link.textContent = f.path;
      link.href = '#';
      link.addEventListener('click', e => onFileClick(f.path, e));
      li.appendChild(link);
      list.appendChild(li);
    });

    fileListContainer.innerHTML = '<h4>Repository Files</h4>';
    fileListContainer.appendChild(list);
  }

  async function onFileClick(path, e) {
    e.preventDefault();
    logStatus(`Fetching ${path}...`);
    try {
      const content = await callApi('/get_file_content', {
        repo_name: observedData.repo_name,
        file_path: path
      });
      const explanation = await callApi('/act', {
        task: "Explain File",
        data: { readme_content: observedData.readme, file_content: content }
      });
      const div = document.createElement('div');
      div.innerHTML = `<h4>${path}</h4><pre>${explanation.result || '[No explanation returned]'}</pre>`;
      finalReport.appendChild(div);
      div.scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
      appError.textContent = err.message;
    }
  }

  // --- Chat Section (with memory + typing + intro cleanup) ---
  chatForm.addEventListener('submit', async e => {
    e.preventDefault();
    const question = chatInput.value.trim();
    if (!question) return;
    appendChat('user', question);
    chatInput.value = '';
    chatInput.disabled = true;

    // Ensure a repo is analyzed first
    if (!observedData || !observedData.readme) {
      appendChat('agent', 'Please analyze a repository first before chatting about it.');
      chatInput.disabled = false;
      chatInput.focus();
      return;
    }

    // Add typing indicator
    const typingIndicator = document.createElement("div");
    typingIndicator.className = "chat-message agent";
    typingIndicator.textContent = "Agent is typing...";
    chatWindow.appendChild(typingIndicator);
    chatWindow.scrollTop = chatWindow.scrollHeight;

    try {
      const res = await callApi('/chat', {
        readme_content: observedData.readme,
        chat_history: chatHistory,
        question
      });

      typingIndicator.remove();

      if (res.error) {
        appendChat('system', `⚠️ ${res.error}`);
      } else if (!res.result || res.result.trim() === '') {
        appendChat('system', '🤖 No response generated — model may have summarized context.');
      } else if (res.result.includes('Summary of previous conversation:')) {
        appendChat('system', '🧠 Memory compressed — older context summarized.');
        appendChat('agent', res.result.replace('Summary of previous conversation:', '').trim());
      } else {
        appendChat('agent', res.result);
      }

      // Prevent repetitive intros
      const lastAgent = chatHistory.at(-1)?.agent || "";
      if (res.result?.startsWith("Hi! I'm a GitHub project expert assistant") &&
          lastAgent.includes("GitHub project expert assistant")) {
        const recentMsg = chatWindow.querySelector(".chat-message.agent:last-child");
        if (recentMsg) recentMsg.remove();
      }

      chatHistory.push({ user: question, agent: res.result || '' });
      localStorage.setItem("chatHistory", JSON.stringify(chatHistory));

    } catch (err) {
      typingIndicator.remove();
      appendChat('system', `Error: ${err.message}`);
    } finally {
      chatInput.disabled = false;
      chatInput.focus();
    }
  });

  function appendChat(sender, text) {
    const msg = document.createElement('div');
    msg.className = `chat-message ${sender}`;
    msg.textContent = text;
    chatWindow.appendChild(msg);
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  // --- Auth ---
  loginForm.addEventListener('submit', e => {
    e.preventDefault();
    const u = document.getElementById('username').value;
    const p = document.getElementById('password').value;
    const auth = new AmazonCognitoIdentity.AuthenticationDetails({ Username: u, Password: p });
    cognitoUser = new AmazonCognitoIdentity.CognitoUser({ Username: u, Pool: userPool });
    cognitoUser.authenticateUser(auth, {
      onSuccess: r => { idToken = r.getIdToken().getJwtToken(); showApp(); },
      onFailure: e => { document.getElementById('login-error').textContent = e.message; },
      newPasswordRequired: () => showNewPasswordForm()
    });
  });

  newPasswordForm.addEventListener('submit', e => {
    e.preventDefault();
    const newPass = document.getElementById('new-password').value;
    cognitoUser.completeNewPasswordChallenge(newPass, {}, {
      onSuccess: r => { idToken = r.getIdToken().getJwtToken(); showApp(); },
      onFailure: e => { document.getElementById('new-password-error').textContent = e.message; }
    });
  });

  logoutButton.addEventListener('click', () => {
    if (cognitoUser) cognitoUser.signOut();
    localStorage.removeItem("chatHistory");
    showLogin();
  });

  function showApp() {
    loginContainer.style.display = 'none';
    newPasswordContainer.style.display = 'none';
    appContainer.style.display = 'block';
  }

  function showNewPasswordForm() {
    loginContainer.style.display = 'none';
    newPasswordContainer.style.display = 'block';
  }

  function showLogin() {
    loginContainer.style.display = 'block';
    newPasswordContainer.style.display = 'none';
    appContainer.style.display = 'none';
  }
})();
