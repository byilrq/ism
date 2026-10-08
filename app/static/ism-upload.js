/* ISM original-image uploader. No canvas, image conversion or recompression. */
(function () {
    'use strict';
    const queues = new Map();
    const hashes = new WeakMap();
    const nativeSubmit = HTMLFormElement.prototype.submit;
    const allowed = /\.(jpe?g|png|webp)$/i;
    const meta = document.querySelector('meta[name="ism-max-content-length"]');
    const maxBytes = Number(meta && meta.content) || 20 * 1024 * 1024;
    const mb = n => (n / 1024 / 1024).toFixed(2) + ' MB';

    async function fingerprint(file) {
        if (hashes.has(file)) return hashes.get(file);
        const task = (async () => {
            if (window.crypto && crypto.subtle && file.arrayBuffer) {
                const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
                return Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, '0')).join('');
            }
            // Do not discard different photos merely because names/sizes match.
            // The server always performs SHA-256, including on plain HTTP.
            return null;
        })();
        hashes.set(file, task);
        return task;
    }

    function notice(form, text, error) {
        let el = form.querySelector('[data-ism-upload-status]');
        if (el) {
            el.textContent = text;
            el.style.color = error ? '#b91c1c' : '#66788a';
            return;
        }
        el = form.querySelector('.ism-upload-status');
        if (!el) {
            el = document.createElement('div');
            el.className = 'ism-upload-status';
            el.setAttribute('role', 'status');
            el.setAttribute('aria-live', 'polite');
            el.style.cssText = 'margin:12px 0;padding:10px 12px;border:1px solid #ccd7e3;border-radius:10px;line-height:1.6;word-break:break-word;';
            form.appendChild(el);
        }
        el.textContent = text;
        el.style.color = error ? '#b91c1c' : '#334155';
    }

    function render(state) {
        const el = state.element;
        el.textContent = '';
        if (!state.files.length) {
            el.textContent = '\u672a\u52a0\u8f7d';
            if (state.message) {
                const hint = document.createElement('div');
                hint.textContent = state.message;
                hint.style.color = '#b45309';
                el.appendChild(hint);
            }
            return;
        }
        const summary = document.createElement('div');
        const total = state.files.reduce((n, item) => n + item.file.size, 0);
        summary.textContent = '\u5df2\u9009 ' + state.files.length + ' \u5f20\u539f\u56fe\uff08\u6700\u591a5\u5f20\uff09\uff0c\u5171 ' + mb(total) + '\uff0c\u4e0d\u538b\u7f29';
        el.appendChild(summary);
        state.files.forEach((item, index) => {
            const row = document.createElement('div');
            row.className = 'upload-preview-item selected-file-item';
            const label = document.createElement('span');
            label.textContent = item.file.name + ' (' + mb(item.file.size) + ')';
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = '\u00d7';
            button.className = 'upload-remove-btn selected-file-remove';
            button.setAttribute('aria-label', '\u5220\u9664\u8be5\u56fe\u7247');
            button.onclick = function () {
                if (state.form && state.form._ismBusy) return;
                state.files.splice(index, 1);
                render(state);
            };
            row.append(label, button);
            el.appendChild(row);
        });
        if (state.message) {
            const hint = document.createElement('div');
            hint.textContent = state.message;
            hint.style.color = '#b45309';
            el.appendChild(hint);
        }
    }

    window.openUploadChooser = function (dialogId) {
        const dialog = document.getElementById(dialogId);
        if (dialog) dialog.classList.add('show');
    };
    window.closeUploadChooser = function (dialogId) {
        const dialog = document.getElementById(dialogId);
        if (dialog) dialog.classList.remove('show');
    };
    window.updateSelectedFiles = function (inputId, textId, dialogId) {
        const input = document.getElementById(inputId);
        const element = document.getElementById(textId);
        if (!input || !element) return Promise.resolve();
        const incoming = Array.from(input.files || []);
        // Never put the entire queue back into a picker: repeated change events
        // from a camera/picker must not append that queue to itself.
        input.value = '';
        if (dialogId) window.closeUploadChooser(dialogId);
        let state = queues.get(textId);
        if (!state) {
            state = {element, form: input.form, files: [], pending: Promise.resolve(), message: ''};
            queues.set(textId, state);
        }
        if (state.form && state.form._ismBusy) return state.pending;
        state.pending = state.pending.then(async function () {
            let duplicates = 0, overflow = 0, invalid = 0;
            for (const file of incoming) {
                if (!allowed.test(file.name) || file.size === 0) { invalid++; continue; }
                const key = await fingerprint(file);
                if (state.files.some(item => item.file === file || (key && item.key === key))) {
                    duplicates++;
                    continue;
                }
                if (state.files.length >= 5) { overflow++; continue; }
                state.files.push({file, key});
            }
            const hints = [];
            if (duplicates) hints.push('\u5df2\u8df3\u8fc7 ' + duplicates + ' \u5f20\u91cd\u590d\u9009\u62e9\u7684\u56fe\u7247');
            if (overflow) hints.push('\u6bcf\u6b21\u6700\u591a5\u5f20\uff0c\u8d85\u51fa\u90e8\u5206\u672a\u52a0\u5165');
            if (invalid) hints.push('\u4ec5\u652f\u6301\u975e\u7a7a JPG/JPEG/PNG/WebP \u539f\u56fe\uff0c\u4e0d\u652f\u6301\u7684\u6587\u4ef6\u672a\u52a0\u5165');
            state.message = hints.join('\uff1b');
            render(state);
        }).catch(function () {
            state.message = '\u8bfb\u53d6\u56fe\u7247\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9';
            render(state);
        });
        return state.pending;
    };

    function imageForm(form) {
        return form && form.querySelector('input[type="file"][name="image_files"]');
    }

    function newToken() {
        if (window.crypto && crypto.getRandomValues) {
            return Array.from(crypto.getRandomValues(new Uint8Array(24)), x => x.toString(16).padStart(2, '0')).join('');
        }
        return Date.now().toString(36) + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    }

    function setBusy(form, busy) {
        form._ismBusy = busy;
        form.setAttribute('aria-busy', busy ? 'true' : 'false');
        if (busy) {
            form._ismButtons = Array.from(form.querySelectorAll('button,input[type="submit"]'))
                .map(el => [el, el.disabled]);
            form._ismButtons.forEach(([el]) => { el.disabled = true; });
        } else {
            (form._ismButtons || []).forEach(([el, wasDisabled]) => { el.disabled = wasDisabled; });
            form._ismButtons = [];
        }
    }

    async function send(form, submitter) {
        if (!imageForm(form)) { nativeSubmit.call(form); return false; }
        if (form._ismBusy) return false;
        if (form.reportValidity && !form.reportValidity()) return false;
        setBusy(form, true);
        try {
            const selected = Array.from(queues.values()).filter(state => state.form === form);
            await Promise.all(selected.map(state => state.pending));
            const payload = new FormData(form);
            if (submitter && submitter.name) payload.append(submitter.name, submitter.value);
            if (selected.length) {
                payload.delete('image_files');
                selected.forEach(state => state.files.forEach(item => payload.append('image_files', item.file, item.file.name)));
            }
            const files = payload.getAll('image_files').filter(file => file instanceof File && file.name);
            if (files.length > 5) throw new Error('\u6bcf\u6b21\u6700\u591a\u4e0a\u4f20 5 \u5f20\u539f\u56fe');
            let size = 0;
            const parts = [];
            for (const [name, value] of payload.entries()) {
                if (name === '_upload_request_id') continue;
                if (value instanceof File) {
                    if (!value.name) continue;
                    size += value.size;
                    parts.push([name, value.name, value.size, value.lastModified, await fingerprint(value)]);
                } else {
                    size += new TextEncoder().encode(value).length;
                    parts.push([name, value]);
                }
            }
            if (size + Math.min(65536, Math.ceil(maxBytes * 0.02)) > maxBytes) {
                throw new Error('\u672c\u6b21\u539f\u56fe\u548c\u8868\u5355\u5171 ' + mb(size) + '\uff0c\u8d85\u51fa\u8bf7\u6c42\u4e0a\u9650 ' + mb(maxBytes) + '\uff08\u542b\u8868\u5355\u5f00\u9500\uff09\u3002\u8bf7\u5206\u6279\u4e0a\u4f20\uff0c\u4e0d\u4f1a\u538b\u7f29\u539f\u56fe\u3002');
            }
            const signature = JSON.stringify(parts);
            if (form._ismSignature !== signature || !form._ismToken) {
                form._ismToken = newToken();
                form._ismSignature = signature;
            }
            payload.set('_upload_request_id', form._ismToken);
            notice(form, '\u6b63\u5728\u4e0a\u4f20\u539f\u56fe\uff0c\u8bf7\u52ff\u91cd\u590d\u70b9\u51fb\u786e\u8ba4\u2026', false);
            const xhr = new XMLHttpRequest();
            form._ismXHR = xhr;
            const declaredAction = form.getAttribute('action');
            const requestTarget = new URL(declaredAction || window.location.pathname, window.location.origin);
            requestTarget.hash = '';
            const requestUrl = requestTarget.href;
            xhr.open((form.method || 'POST').toUpperCase(), requestUrl, true);
            xhr.setRequestHeader('X-ISM-Upload', '1');
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.setRequestHeader('Accept', 'application/json');
            xhr.timeout = 600000;
            xhr.upload.onprogress = function (event) {
                if (!event.lengthComputable) return;
                const percent = Math.min(100, Math.floor(event.loaded * 100 / event.total));
                notice(form, percent < 100
                    ? '\u539f\u56fe\u4e0a\u4f20 ' + percent + '%\uff08' + mb(event.loaded) + ' / ' + mb(event.total) + '\uff09'
                    : '\u5df2\u4e0a\u4f20\u81f3\u670d\u52a1\u5668\uff0c\u6b63\u5728\u8fdb\u5165\u540e\u53f0\u5b58\u50a8\u961f\u5217\u2026', false);
            };
            const uncertain = function () {
                setBusy(form, false);
                notice(form, '\u672a\u6536\u5230\u4fdd\u5b58\u786e\u8ba4\uff0c\u670d\u52a1\u5668\u53ef\u80fd\u5df2\u4fdd\u5b58\u3002\u8bf7\u7f51\u7edc\u6062\u590d\u540e\u518d\u70b9\u51fb\u786e\u8ba4\uff1b\u91cd\u8bd5\u4f1a\u590d\u7528\u672c\u6b21\u63d0\u4ea4\u7f16\u53f7\uff0c\u907f\u514d\u91cd\u590d\u65b0\u589e\u56fe\u7247\u3002', true);
            };
            xhr.onerror = uncertain;
            xhr.ontimeout = uncertain;
            xhr.onabort = uncertain;
            xhr.onload = function () {
                let data = null;
                try { data = JSON.parse(xhr.responseText); } catch (_) { /* HTML errors supported below. */ }
                if (xhr.status >= 200 && xhr.status < 300 && data && data.ok && data.redirect_url) {
                    const target = new URL(data.redirect_url, window.location.href);
                    if (target.origin !== window.location.origin) { uncertain(); return; }
                    // Local spool + DB task are durable at this point. Final cloud/mount
                    // synchronization is handled by the background worker. Image uploads
                    // intentionally finish without a positive toast.
                    if (files.length) target.searchParams.delete('saved');
                    window.location.assign(target.href);
                    return;
                }
                // Backward-compatible handover during a manual code replacement:
                // an older Gunicorn process may still return a normal 303 -> HTML
                // detail page while the new static JS is already live via Nginx.
                // Only accept the old response as success when the final same-origin
                // URL carries the application's explicit saved=1 marker.
                if (xhr.status >= 200 && xhr.status < 300 && xhr.responseURL) {
                    try {
                        const legacyTarget = new URL(xhr.responseURL, window.location.href);
                        if (legacyTarget.origin === window.location.origin && legacyTarget.searchParams.get('saved') === '1') {
                            if (files.length) legacyTarget.searchParams.delete('saved');
                            window.location.assign(legacyTarget.href);
                            return;
                        }
                    } catch (_) { /* fall through to the normal error path */ }
                }
                setBusy(form, false);
                if (data && data.renew_token) form._ismToken = null;
                let message = data && data.error;
                if (!message && xhr.status === 413) message = '\u8bf7\u6c42\u8d85\u51fa\u670d\u52a1\u5668\u4e0a\u9650\uff0c\u8bf7\u51cf\u5c11\u672c\u6b21\u56fe\u7247\u6570\u91cf\u540e\u91cd\u8bd5';
                if (!message && xhr.responseText) {
                    const html = new DOMParser().parseFromString(xhr.responseText, 'text/html');
                    const error = html.querySelector('.err,.error,.error-msg,.error-message');
                    if (error) message = error.textContent.trim();
                    if (!message && /\/login(?:\?|$)/.test(xhr.responseURL)) message = '\u767b\u5f55\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55\u540e\u518d\u4e0a\u4f20';
                }
                if (!message && xhr.status) {
                    let requestPath = '';
                    let finalPath = '';
                    try { requestPath = new URL(requestUrl).pathname; } catch (_) {}
                    try { finalPath = xhr.responseURL ? new URL(xhr.responseURL).pathname : ''; } catch (_) {}
                    const routeHint = finalPath && finalPath !== requestPath
                        ? '\uff0c\u63d0\u4ea4\u5730\u5740 ' + requestPath + '\uff0c\u6700\u7ec8\u5730\u5740 ' + finalPath
                        : (requestPath ? '\uff0c\u63d0\u4ea4\u5730\u5740 ' + requestPath : '');
                    message = '\u670d\u52a1\u5668\u672a\u786e\u8ba4\u4fdd\u5b58\u6210\u529f\uff08HTTP ' + xhr.status + routeHint + '\uff09\u3002\u8bf7\u91cd\u8bd5\uff1b\u91cd\u8bd5\u4f1a\u81ea\u52a8\u9632\u6b62\u540c\u4e00\u5f20\u56fe\u7247\u91cd\u590d\u4fdd\u5b58\u3002';
                }
                notice(form, message || '\u672a\u6536\u5230\u4fdd\u5b58\u786e\u8ba4\uff0c\u8bf7\u91cd\u8bd5\uff08\u91cd\u8bd5\u4f1a\u81ea\u52a8\u9632\u91cd\u590d\uff09', true);
            };
            xhr.send(payload);
        } catch (error) {
            setBusy(form, false);
            notice(form, error.message || '\u51c6\u5907\u4e0a\u4f20\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5', true);
        }
        return false;
    }

    window.ISMUpload = {submit: send};
    document.addEventListener('submit', function (event) {
        if (!event.defaultPrevented && imageForm(event.target)) {
            event.preventDefault();
            send(event.target, event.submitter);
        }
    });
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) document.querySelectorAll('form').forEach(form => setBusy(form, false));
    });
}());
