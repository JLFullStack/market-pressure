// Página Conta (só na versão publicada): troca da própria senha e, para o administrador, a
// gestão de usuários. As regras (CPF, e-mail, tamanho da senha, permissões) ficam no Worker
// (worker/auth.js); aqui só se enviam os formulários e se mostram as respostas.

const $ = (id) => document.getElementById(id);

// {status, data} de uma rota /auth (o Worker; não passa pelo Python).
async function authCall(method, path, body) {
    const init = { method, cache: 'no-store' };
    if (body !== undefined) {
        init.headers = { 'Content-Type': 'application/json' };
        init.body = JSON.stringify(body);
    }
    try {
        const response = await fetch(`/auth${path}`, init);
        if (response.status === 401) toLogin();
        return { status: response.status, data: await response.json().catch(() => ({})) };
    } catch (e) {
        return { status: 0, data: { message: 'sem conexão com o servidor' } };
    }
}

function showMsg(el, ok, text) {
    el.className = `form-msg ${ok ? 'ok' : 'error'}`;
    el.textContent = text;
}

const fmtCpf = (cpf) => /^\d{11}$/.test(cpf) ? `${cpf.slice(0, 3)}.${cpf.slice(3, 6)}.${cpf.slice(6, 9)}-${cpf.slice(9)}` : cpf;
const fmtDay = (ms) => new Date(ms).toLocaleDateString('pt-BR');

// Envia o formulário; limpa os campos de senha depois (deu certo ou não).
async function submit(form, msgEl, method, path, body, onOk) {
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    const { status, data } = await authCall(method, path, body);
    button.disabled = false;
    form.querySelectorAll('input[type="password"]').forEach(input => { input.value = ''; });
    const ok = status >= 200 && status < 300;
    showMsg(msgEl, ok, ok ? data.message : (data.message || `HTTP ${status}`));
    if (ok && onOk) await onOk();
}

// ---------------------------------------------------------------- Minha conta

$('passwordForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    if (form.password.value !== form.confirm.value) {
        showMsg($('passwordMsg'), false, 'A nova senha e a repetição não conferem.');
        return;
    }
    submit(form, $('passwordMsg'), 'POST', '/password', { current: form.current.value, password: form.password.value });
});

// ---------------------------------------------------------------- Usuários (administrador)

async function loadUsers() {
    const { status, data } = await authCall('GET', '/users');
    const body = $('usersBody');
    if (status !== 200) {
        body.innerHTML = `<tr><td colspan="6" class="empty-row">${escapeHTML(data.message || `HTTP ${status}`)}</td></tr>`;
        return;
    }
    body.innerHTML = data.users.map(u => `
        <tr>
            <td>${escapeHTML(u.name)}</td>
            <td class="nowrap">${escapeHTML(fmtCpf(u.cpf))}</td>
            <td>${escapeHTML(u.email)}</td>
            <td>${u.admin ? '<span class="badge badge-ok">Administrador</span>' : '<span class="badge badge-low">Usuário</span>'}</td>
            <td class="nowrap">${fmtDay(u.createdAt)}</td>
            <td class="c">${u.admin ? '—' : `<button type="button" class="link-btn" data-delete="${escapeHTML(u.id)}" data-name="${escapeHTML(u.name)}">Excluir</button>`}</td>
        </tr>`).join('');
    $('resetUser').innerHTML = data.users
        .map(u => `<option value="${escapeHTML(u.id)}">${escapeHTML(u.name)} (${escapeHTML(u.email)})</option>`).join('');
}

$('usersBody').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-delete]');
    if (!button) return;
    const name = button.dataset.name;
    if (!confirm(`Excluir a conta de ${name}? As carteiras dessa pessoa também serão apagadas, sem volta.`)) return;
    const { status, data } = await authCall('DELETE', `/users?id=${encodeURIComponent(button.dataset.delete)}`);
    const ok = status === 200;
    showMsg($('usersMsg'), ok, ok ? data.message : (data.message || `HTTP ${status}`));
    if (ok) await loadUsers();
});

$('newUserForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    const f = form.elements;
    const body = { name: f.name.value, cpf: f.cpf.value, email: f.email.value, password: f.password.value };
    submit(form, $('newUserMsg'), 'POST', '/users', body, async () => {
        form.reset();
        await loadUsers();
    });
});

$('resetForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const form = event.target;
    submit(form, $('resetMsg'), 'POST', '/users/password', { id: $('resetUser').value, password: form.elements.password.value });
});

// ---------------------------------------------------------------- Início

if (!IN_BROWSER) {
    $('localNote').hidden = false;
} else {
    session.then(user => {
        if (!user) return;
        $('accountInfo').textContent = `${user.name} · ${user.email}${user.admin ? ' · administrador' : ''}`;
        if (user.admin) {
            $('adminSection').hidden = false;
            loadUsers();
        }
    });
}
