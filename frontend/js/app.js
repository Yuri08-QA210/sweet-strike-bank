/* ============================================
   Sweet-Strike Bank - Application Logic
   ============================================ */

const app = {
  // --- State ---
  state: {
    currentView: 'landing',
    sessionToken: null,
    username: null,
    currentTab: 'overview',
    currentSubTabs: {
      adcs: 'adcs-templates',
      swift: 'swift-hsm'
    },
    swiftScheme: 'pkcs1v15',
    swiftCurrency: 'USD',
    hsmInitialized: false,
    edrPollingInterval: null,
    isRegister: false,
    apiUrl: ''  // Same origin by default; override if needed
  },

  // --- Initialization ---
  init() {
    // Check for existing session
    const token = sessionStorage.getItem('ssb_token');
    const user = sessionStorage.getItem('ssb_user');
    if (token && user) {
      this.state.sessionToken = token;
      this.state.username = user;
      this.showView('dashboard');
    }
    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
      const menu = document.getElementById('user-menu');
      const dropdown = document.getElementById('user-dropdown');
      if (menu && !menu.contains(e.target)) {
        dropdown.classList.add('hidden');
      }
    });
  },

  // --- View Management ---
  showView(viewName) {
    // Hide all views
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    // Show target view
    const target = document.getElementById('view-' + viewName);
    if (target) {
      target.classList.add('active');
      this.state.currentView = viewName;
    }
    // Start/stop EDR polling
    if (viewName === 'dashboard' && this.state.currentTab === 'edr') {
      this.startEdrPolling();
    } else {
      this.stopEdrPolling();
    }
  },

  // --- Tab Management ---
  switchTab(tabName) {
    this.state.currentTab = tabName;
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(tc => {
      tc.classList.remove('active');
    });
    const target = document.getElementById('tab-' + tabName);
    if (target) target.classList.add('active');

    // EDR polling
    if (tabName === 'edr') {
      this.refreshEdr();
      this.startEdrPolling();
    } else {
      this.stopEdrPolling();
    }
  },

  // --- Sub-Tab Management ---
  switchSubTab(parent, subtabId) {
    this.state.currentSubTabs[parent] = subtabId;
    // Find the parent section
    const parentSection = document.getElementById('tab-' + parent);
    if (!parentSection) return;
    // Update sub-tab buttons
    parentSection.querySelectorAll('.sub-tab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.subtab === subtabId);
    });
    // Update sub-tab content
    parentSection.querySelectorAll('.subtab-content').forEach(stc => {
      stc.classList.remove('active');
    });
    const target = document.getElementById(subtabId);
    if (target) target.classList.add('active');
  },

  // --- Role Restrictions ---
  applyRoleRestrictions(groups) {
    const privilegedGroups = [
      'IT_Interns', 'Workstation_Admins', 'Certificate_Managers',
      'Enterprise_Admins', 'Domain_Admins', 'SWIFT_Operators', 'HSM_Admins',
      'Server_Operators', 'Helpdesk'
    ];
    const isPrivileged = groups.some(g => privilegedGroups.includes(g));

    const restrictedTabs = ['network', 'adcs', 'swift', 'edr'];
    restrictedTabs.forEach(tab => {
      const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (btn) {
        btn.style.display = isPrivileged ? '' : 'none';
      }
    });

    // If current tab is now hidden, switch back to overview
    if (!isPrivileged && restrictedTabs.includes(this.state.currentTab)) {
      this.switchTab('overview');
    }
  },

  // --- Login ---
  async handleLogin(e) {
    e.preventDefault();
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const errorEl = document.getElementById('login-error');

    if (!username || !password) {
      this.showError(errorEl, 'Please enter username and password');
      return;
    }

    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.textContent = 'Signing in...';
    errorEl.classList.add('hidden');

    try {
      const res = await this.apiCall('/web/login', 'POST', { username, password });
      const data = await res.json();

      if (res.ok && data.session_token) {
        this.state.sessionToken = data.session_token;
        this.state.username = username;
        sessionStorage.setItem('ssb_token', data.session_token);
        sessionStorage.setItem('ssb_user', username);

        // Update dashboard
        document.getElementById('user-display-name').textContent = username;
        document.getElementById('user-avatar').textContent = username.charAt(0).toUpperCase();
        document.getElementById('total-balance').textContent = data.balance ? '$' + Number(data.balance).toLocaleString('en-US', {minimumFractionDigits: 2}) : '$0.00';
        document.getElementById('account-type').textContent = data.account_type || 'Standard';

        this.showView('dashboard');

        // Apply tab restrictions based on user's groups
        this.applyRoleRestrictions(data.groups || []);
      } else {
        this.showError(errorEl, data.error || data.message || 'Invalid credentials');
      }
    } catch (err) {
      this.showError(errorEl, 'Connection error. Please try again.');
    }

    btn.disabled = false;
    btn.textContent = 'Sign In';
  },

  // --- Register / Switch ---
  switchToRegister() {
    this.state.isRegister = true;
    document.getElementById('login-title').textContent = 'Open New Account';
    document.getElementById('login-subtitle').textContent = 'Create your secure banking account';
    document.getElementById('login-btn').textContent = 'Create Account';
    document.getElementById('login-switch-text').textContent = 'Already have an account?';
    document.getElementById('login-switch-link').textContent = 'Sign In';
    document.getElementById('login-switch-link').onclick = () => { app.switchToLogin(); return false; };
  },

  switchToLogin() {
    this.state.isRegister = false;
    document.getElementById('login-title').textContent = 'Sign In';
    document.getElementById('login-subtitle').textContent = 'Access your secure banking portal';
    document.getElementById('login-btn').textContent = 'Sign In';
    document.getElementById('login-switch-text').textContent = "Don't have an account?";
    document.getElementById('login-switch-link').textContent = 'Open New Account';
    document.getElementById('login-switch-link').onclick = () => { app.switchToRegister(); return false; };
  },

  // --- Logout ---
  handleLogout() {
    this.state.sessionToken = null;
    this.state.username = null;
    sessionStorage.removeItem('ssb_token');
    sessionStorage.removeItem('ssb_user');
    this.stopEdrPolling();
    this.showView('landing');
    // Reset form
    document.getElementById('username').value = '';
    document.getElementById('password').value = '';
    document.getElementById('login-error').classList.add('hidden');
    this.switchToLogin();
    // Reset tabs visibility on logout
    ['network', 'adcs', 'swift', 'edr'].forEach(tab => {
      const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
      if (btn) btn.style.display = '';
    });
  },

  // --- Password Toggle ---
  togglePassword() {
    const input = document.getElementById('password');
    const eyeOn = document.getElementById('eye-icon');
    const eyeOff = document.getElementById('eye-off-icon');
    if (input.type === 'password') {
      input.type = 'text';
      eyeOn.classList.add('hidden');
      eyeOff.classList.remove('hidden');
    } else {
      input.type = 'password';
      eyeOn.classList.remove('hidden');
      eyeOff.classList.add('hidden');
    }
  },

  // --- User Menu ---
  toggleUserMenu() {
    document.getElementById('user-dropdown').classList.toggle('hidden');
  },

  // --- Account ---
  async openAccount() {
    const type = document.querySelector('input[name="account_type"]:checked')?.value || 'standard';
    const holder = document.getElementById('holder-name').value.trim();
    const resultEl = document.getElementById('account-result');

    if (!holder) {
      this.showResult(resultEl, 'Error: Account holder name is required');
      return;
    }

    try {
      const res = await this.apiCall('/web/account/open', 'POST', {
        session_token: this.state.sessionToken,
        account_type: type,
        holder: holder
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));

      // If staff account opened successfully, re-apply role restrictions
      if (res.ok && data.type === 'staff' && data.flag) {
        this.applyRoleRestrictions(['IT_Interns']);
      }
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  // --- Network ---
  async scanNetwork(vlan) {
    const tbody = document.getElementById('scan-results');
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Scanning...</td></tr>';

    try {
      const res = await this.apiCall(`/web/scan?vlan=${vlan}&session_token=${this.state.sessionToken}`, 'GET');
      const data = await res.json();

      if (data.hosts && Array.isArray(data.hosts)) {
        tbody.innerHTML = data.hosts.map(h => `
          <tr>
            <td>${this.escHtml(h.hostname || 'Unknown')}</td>
            <td class="mono">${this.escHtml(h.ip || '')}</td>
            <td class="mono">${this.escHtml(h.ports || '')}</td>
            <td>${this.escHtml(h.services || '')}</td>
            <td><span class="status-dot ${h.status === 'up' ? 'green' : 'red'}"></span></td>
          </tr>
        `).join('');
      } else {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No hosts found</td></tr>';
        this.showResult(null, JSON.stringify(data, null, 2));
      }
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="text-center text-red">Error: ${err.message}</td></tr>`;
    }
  },

  async executePivot() {
    const from = document.getElementById('pivot-from').value.trim();
    const to = document.getElementById('pivot-to').value.trim();
    const creds = document.getElementById('pivot-creds').value.trim();
    const resultEl = document.getElementById('pivot-result');

    if (!from || !to) {
      this.showResult(resultEl, 'Error: Source and target hosts required');
      return;
    }

    try {
      const res = await this.apiCall('/network/pivot', 'POST', {
        session_token: this.state.sessionToken,
        from: from,
        to: to,
        credentials: creds
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  // --- ADCS ---
  async loadAdcsTemplates() {
    const tbody = document.getElementById('adcs-templates-body');
    try {
      const res = await this.apiCall(`/adcs/templates?session_id=${this.state.sessionToken}`, 'GET');
      const data = await res.json();
      if (data.templates && Array.isArray(data.templates)) {
        tbody.innerHTML = data.templates.map(t => `
          <tr>
            <td>${this.escHtml(t.name || t.template || '')}</td>
            <td>${t.enabled ? '<span class="status-badge completed">Yes</span>' : '<span class="status-badge pending">No</span>'}</td>
            <td>${this.escHtml(t.enroll || '')}</td>
            <td>${this.escHtml(t.auth || '')}</td>
            <td>${this.escHtml(t.eku || '')}</td>
          </tr>
        `).join('');
      }
    } catch (err) {
      console.error('ADCS templates error:', err);
    }
  },

  async adcsCertRequest() {
    const template = document.getElementById('adcs-template').value;
    const subject = document.getElementById('adcs-subject').value.trim();
    const san = document.getElementById('adcs-san').value.trim();
    const resultEl = document.getElementById('adcs-cert-result');

    try {
      const res = await this.apiCall('/adcs/cert/request', 'POST', {
        session_token: this.state.sessionToken,
        template: template,
        subject: subject,
        san: san
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  async adcsCustomProtocol() {
    const version = document.getElementById('icpr-version').value.trim();
    const asn1 = document.getElementById('asn1-header').value.trim();
    const mic = document.getElementById('ntlm-mic').value.trim();
    const payload = document.getElementById('icpr-payload').value.trim();
    const resultEl = document.getElementById('adcs-protocol-result');

    try {
      const res = await this.apiCall('/adcs/auth', 'POST', {
        session_token: this.state.sessionToken,
        version: version,
        asn1_header: asn1,
        ntlm_mic: mic,
        payload: payload
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  async adcsGetCaFlags() {
    const resultEl = document.getElementById('adcs-ca-result');
    try {
      const res = await this.apiCall('/adcs/ca/manage', 'POST', {
        session_token: this.state.sessionToken,
        action: 'get_flags'
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  async adcsSetCaFlags() {
    const flags = document.getElementById('ca-flags').value.trim();
    const resultEl = document.getElementById('adcs-ca-result');
    try {
      const res = await this.apiCall('/adcs/ca/manage', 'POST', {
        session_token: this.state.sessionToken,
        action: 'set_flags',
        flags: flags
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  async adcsShadowCreds() {
    const target = document.getElementById('shadow-target').value.trim();
    const cert = document.getElementById('shadow-cert').value.trim();
    const resultEl = document.getElementById('adcs-shadow-result');

    try {
      const res = await this.apiCall('/adcs/shadow-creds', 'POST', {
        session_token: this.state.sessionToken,
        target: target,
        certificate: cert
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  async adcsRelay() {
    const spn = document.getElementById('relay-spn').value.trim();
    const token = document.getElementById('relay-token').value.trim();
    const resultEl = document.getElementById('adcs-relay-result');

    try {
      const res = await this.apiCall('/adcs/relay', 'POST', {
        session_token: this.state.sessionToken,
        spn: spn,
        auth_token: token
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  // --- Auto-Tools ---
  async runAutoTool(tool) {
    const resultEl = document.getElementById('autotool-result');
    const endpoint = tool === 'certipy' ? '/api/autotool/certipy' : '/api/autotool/impacket';

    try {
      const res = await this.apiCall(endpoint, 'POST', {
        session_token: this.state.sessionToken
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  // --- SWIFT ---
  async swiftHsmInit() {
    const pin = document.getElementById('hsm-pin').value;
    const resultEl = document.getElementById('swift-hsm-result');

    try {
      const res = await this.apiCall('/swift/hsm/init', 'POST', {
        session_token: this.state.sessionToken,
        pin: pin
      });
      const data = await res.json();

      if (res.ok) {
        this.state.hsmInitialized = true;
        const indicator = document.getElementById('hsm-status-indicator');
        indicator.classList.remove('inactive');
        indicator.classList.add('active');
        indicator.innerHTML = '<span class="status-dot green"></span> Active';
      }

      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  selectScheme(btn, scheme) {
    this.state.swiftScheme = scheme;
    const group = btn.closest('.btn-group');
    group.querySelectorAll('.btn-sm').forEach(b => {
      b.classList.remove('btn-group-active');
      if (!b.classList.contains('btn-danger')) {
        b.classList.add('btn-outline');
      }
    });
    btn.classList.add('btn-group-active');
    btn.classList.remove('btn-outline');
  },

  async swiftSignMessage() {
    const message = document.getElementById('swift-message').value.trim();
    const resultEl = document.getElementById('swift-sign-result');

    if (!message) {
      this.showResult(resultEl, 'Error: Message content required');
      return;
    }

    try {
      const res = await this.apiCall('/swift/hsm/sign', 'POST', {
        session_token: this.state.sessionToken,
        message: message,
        scheme: this.state.swiftScheme
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  selectCurrency(btn, currency) {
    this.state.swiftCurrency = currency;
    const group = btn.closest('.btn-group');
    group.querySelectorAll('.btn-sm').forEach(b => {
      b.classList.remove('btn-group-active');
      b.classList.add('btn-outline');
    });
    btn.classList.add('btn-group-active');
    btn.classList.remove('btn-outline');
  },

  async swiftTransfer() {
    const fromBic = document.getElementById('swift-from-bic').value.trim();
    const toBic = document.getElementById('swift-to-bic').value.trim();
    const amount = document.getElementById('swift-amount').value;
    const signature = document.getElementById('swift-signature').value.trim();
    const resultEl = document.getElementById('swift-transfer-result');

    if (!fromBic || !toBic || !amount) {
      this.showResult(resultEl, 'Error: BIC codes and amount required');
      return;
    }

    try {
      const res = await this.apiCall('/swift/transfer', 'POST', {
        session_token: this.state.sessionToken,
        from_bic: fromBic,
        to_bic: toBic,
        amount: parseFloat(amount),
        currency: this.state.swiftCurrency,
        signature: signature
      });
      const data = await res.json();
      this.showResult(resultEl, JSON.stringify(data, null, 2));

      // Update history if successful
      if (res.ok) {
        this.refreshSwiftHistory();
      }
    } catch (err) {
      this.showResult(resultEl, 'Error: ' + err.message);
    }
  },

  refreshSwiftHistory() {
    // History is pre-populated; in real app, fetch from API
  },

  // --- EDR ---
  async refreshEdr() {
    try {
      const res = await this.apiCall('/edr/', 'GET');
      const data = await res.json();

      if (data) {
        document.getElementById('edr-alerts').textContent = data.alerts ?? '0';
        document.getElementById('edr-banned').textContent = data.banned_ips ?? '0';
        document.getElementById('edr-processes').textContent = data.active_processes ?? '0';
        document.getElementById('edr-quarantined').textContent = data.quarantined ?? '0';

        if (data.paranoia_level) {
          const level = Math.min(5, Math.max(1, data.paranoia_level));
          document.getElementById('paranoia-fill').style.width = (level / 5 * 100) + '%';
          document.getElementById('paranoia-value').textContent = level;
        }

        const lockdownBadge = document.getElementById('lockdown-status');
        if (data.lockdown) {
          lockdownBadge.textContent = 'Active';
          lockdownBadge.classList.remove('inactive');
          lockdownBadge.classList.add('active');
        } else {
          lockdownBadge.textContent = 'Inactive';
          lockdownBadge.classList.remove('active');
          lockdownBadge.classList.add('inactive');
        }

        // Alerts
        if (data.recent_alerts && Array.isArray(data.recent_alerts)) {
          const alertList = document.getElementById('edr-alerts-list');
          alertList.innerHTML = data.recent_alerts.map(a => `
            <div class="alert-item">
              <span class="alert-severity ${a.severity || 'low'}"></span>
              <div class="alert-info">
                <span class="alert-msg">${this.escHtml(a.message || a.msg || '')}</span>
                <span class="alert-time">${this.escHtml(a.time || a.timestamp || '')}</span>
              </div>
            </div>
          `).join('');
        }
      }
    } catch (err) {
      console.error('EDR status error:', err);
    }

    // Always fetch compromised users
    this.fetchCompromisedUsers();
  },

  startEdrPolling() {
    this.stopEdrPolling();
    this.state.edrPollingInterval = setInterval(() => {
      this.fetchCompromisedUsers();
    }, 5000);
  },

  stopEdrPolling() {
    if (this.state.edrPollingInterval) {
      clearInterval(this.state.edrPollingInterval);
      this.state.edrPollingInterval = null;
    }
  },

  async fetchCompromisedUsers() {
    try {
      const res = await this.apiCall('/api/compromised-users', 'GET');
      const data = await res.json();
      const container = document.getElementById('compromised-users-list');

      if (data.users && Array.isArray(data.users) && data.users.length > 0) {
        container.innerHTML = data.users.map(u => `
          <div class="compromised-user-item">
            <span class="skull-icon">&#9760;</span>
            <div class="compromised-info">
              <span class="compromised-ip">${this.escHtml(u.ip || u.source_ip || 'Unknown')}</span>
              <span class="compromised-tool">Tool: ${this.escHtml(u.tool || u.tool_used || 'Unknown')}</span>
              <span class="compromised-time">${this.escHtml(u.timestamp || u.time || '')}</span>
            </div>
            <span class="compromised-badge">COMPROMISED</span>
          </div>
        `).join('');
      } else {
        container.innerHTML = '<p class="text-muted text-center">No compromised users detected</p>';
      }
    } catch (err) {
      console.error('Compromised users error:', err);
    }
  },

  // --- API Helper ---
  async apiCall(path, method = 'GET', body = null) {
    const url = this.state.apiUrl + path;
    const options = {
      method: method,
      headers: {
        'Content-Type': 'application/json',
      },
      credentials: 'include'
    };

    if (body && method !== 'GET') {
      options.body = JSON.stringify(body);
    }

    const res = await fetch(url, options);
    return res;
  },

  // --- UI Helpers ---
  showError(el, msg) {
    if (!el) return;
    el.textContent = msg;
    el.classList.remove('hidden');
  },

  showResult(el, content) {
    if (!el) return;
    el.classList.remove('hidden');
    el.innerHTML = `<button class="copy-btn" onclick="app.copyResult(this)">Copy</button><pre>${this.escHtml(content)}</pre>`;
  },

  copyResult(btn) {
    const pre = btn.parentElement.querySelector('pre');
    if (pre) {
      navigator.clipboard.writeText(pre.textContent).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
      }).catch(() => {
        // Fallback
        const range = document.createRange();
        range.selectNode(pre);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
        document.execCommand('copy');
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
      });
    }
  },

  showToast(msg) {
    const toast = document.getElementById('toast');
    document.getElementById('toast-msg').textContent = msg;
    toast.classList.remove('hidden');
    setTimeout(() => { toast.classList.add('hidden'); }, 3000);
  },

  escHtml(str) {
    if (typeof str !== 'string') return String(str);
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
};

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
  app.init();
});
