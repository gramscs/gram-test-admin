window.adminUI = {
    async request(url, data, method) {
        const response = await fetch(url, {method: method || 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}, body: JSON.stringify(data)});
        const result = await response.json();
        if (!response.ok || result.success === false) throw new Error(result.message || result.error || 'Request failed.');
        return result;
    },
    alert(id, message) {
        const element = document.getElementById(id);
        element.textContent = message;
        element.classList.toggle('d-none', !message);
    },
    downloadForm(url, fields) {
        const form = document.createElement('form');
        form.method = 'POST'; form.action = url; form.hidden = true;
        Object.entries(fields).forEach(([name, value]) => {
            (Array.isArray(value) ? value : [value]).forEach(item => { const input = document.createElement('input'); input.name = name; input.value = item; form.append(input); });
        });
        document.body.append(form); form.submit(); form.remove();
    }
};
