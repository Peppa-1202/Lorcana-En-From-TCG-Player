(() => {
    const origin = 'https://lorcanaplayer.com';
    const storageKey = 'lorcanaplayer-import-v1';
    const toPayload = (cards) => JSON.stringify({version: 1, cards});
    const listPath = '/cards/';
    if (location.origin !== origin) {
        throw new Error('请打开 lorcanaplayer.com 的目录或卡牌详情页。');
    }

    function readCard(source, url) {
        const article = source.querySelector('main article.type-card');
        const id = article?.id.match(/^post-([1-9][0-9]*)$/)?.[1];
        const title = article?.querySelector('h1')?.textContent.trim();
        if (!id || !title) {
            throw new Error('未读到卡牌详情。等浏览器完成加载后再运行。');
        }

        const fields = Object.create(null);
        for (const label of article.querySelectorAll('.card-details .card-label')) {
            const parts = [];
            for (let sibling = label.nextElementSibling; sibling; sibling = sibling.nextElementSibling) {
                const content = sibling.cloneNode(true);
                for (const icon of content.querySelectorAll('img')) {
                    const alt = icon.getAttribute('alt') || icon.getAttribute('title');
                    if (!alt) {
                        throw new Error('发现没有文本标签的技能图标，请检查这张卡。');
                    }
                    icon.replaceWith('[' + alt + ']');
                }
                for (const br of content.querySelectorAll('br')) {
                    br.replaceWith('\n');
                }
                parts.push(content.textContent.replace(/[ \t\r\f\v]+/g, ' '));
            }
            fields[label.textContent.trim()] = parts.join('\n\n').trim();
        }
        if (!fields.Name || !fields.Set || !fields['Card Type']) {
            throw new Error('详情缺少卡名、系列或卡牌类型。');
        }

        const image = article.querySelector('img.card');
        const imageUrl = image?.parentElement?.matches('a[href]')
            ? image.parentElement.getAttribute('href') : image?.getAttribute('src');
        const dateParts = new Intl.DateTimeFormat('en-GB', {
            timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
        }).formatToParts(new Date());
        const { year, month, day } = Object.fromEntries(dateParts.map(({type, value}) => [type, value]));
        return {
            id, title, fields, image_url: imageUrl ? new URL(imageUrl, url).href : '',
            url,
            captured_on: [year, month, day].join('-'),
        };
    }

    if (/^\/card\/[^/]+\/?$/.test(location.pathname)) {
        const card = readCard(document, location.origin + location.pathname);
        const {id, title} = card;
        const batch = JSON.parse(sessionStorage.getItem(storageKey) ?? '{"version":1,"cards":[]}');
        const index = batch.cards.findIndex((record) => record.id === id);
        if (index < 0) {
            batch.cards.push(card);
        } else {
            batch.cards[index] = card;
        }

        const payload = JSON.stringify(batch);
        if (new TextEncoder().encode(payload).length > 60000) {
            throw new Error('已达到单批大小上限。先提交剪贴板里的上一批，清空后再收集。');
        }
        sessionStorage.setItem(storageKey, payload);
        copy(payload);
        console.info('已复制本批 ' + batch.cards.length + ' 张卡的 JSON。最后加入：' + title + '（' + id + '）');
        return;
    }
    if (location.pathname !== listPath || location.search) {
        throw new Error('批量检查请打开未筛选的目录 https://lorcanaplayer.com/cards/ 。');
    }

    function readList(source) {
        const total = Number(source.querySelector('[data-wpc-total-found-posts]')
            ?.getAttribute('data-wpc-total-found-posts'));
        const cards = [...source.querySelectorAll('main article.type-card')].map((article) => {
            const id = article.id.match(/^post-([1-9][0-9]*)$/)?.[1];
            const link = article.querySelector('h5 a[href]');
            const url = new URL(link?.getAttribute('href') || '/', origin);
            if (!id || url.origin !== origin || !/^\/card\/[^/]+\/?$/.test(url.pathname)
                || url.search || url.hash) {
                throw new Error('目录中的卡牌 ID 或详情链接格式变化。');
            }
            return {id, url: url.href, title: link.textContent.trim()};
        });
        const next = source.querySelector('a.next.page-numbers[href]')?.getAttribute('href');
        if (!Number.isSafeInteger(total) || total < 1 || !cards.length) {
            throw new Error('当前页面缺少正常的卡牌目录。若出现验证提示，请先正常打开网页。');
        }
        return {total, cards, next};
    }

    async function getPage(url, signal) {
        // Keep requests sequential and stop when the site declines access.
        await new Promise((resolve) => setTimeout(resolve, 450));
        const response = await fetch(url, {
            credentials: 'same-origin', redirect: 'error',
            signal: AbortSignal.any([signal, AbortSignal.timeout(30000)]),
        });
        if (!response.ok) {
            throw new Error('网站返回 HTTP ' + response.status + '。已停止请求。');
        }
        return new DOMParser().parseFromString(await response.text(), 'text/html');
    }

    async function findMissing(known, signal, report) {
        const first = readList(document);
        const byId = new Map();
        const seenUrls = new Set();
        let page = first;
        let number = 1;
        for (;;) {
            if (page.total !== first.total) {
                throw new Error('目录总数在检查期间变化，请刷新后重试。');
            }
            for (const card of page.cards) {
                const canonical = card.url.replace(/\/$/, '');
                if (byId.has(card.id) || seenUrls.has(canonical)) {
                    throw new Error('目录分页出现重复记录，请刷新后重试。');
                }
                byId.set(card.id, card);
                seenUrls.add(canonical);
            }
            report('已检查 ' + number + ' 页，' + byId.size + ' / ' + first.total + ' 张。');
            if (!page.next) break;
            const next = new URL(page.next, origin);
            number += 1;
            if (byId.size >= first.total || next.href !== origin + '/cards/page/' + number + '/') {
                throw new Error('目录下一页链接或页数异常。');
            }
            page = readList(await getPage(next.href, signal));
        }
        if (byId.size !== first.total) {
            throw new Error('实际读取 ' + byId.size + ' 张，网站显示 ' + first.total + ' 张，目录检查未完成。');
        }
        const final = readList(await getPage(origin + listPath, signal));
        if (final.total !== first.total || JSON.stringify(final.cards) !== JSON.stringify(first.cards)) {
            throw new Error('检查期间网站第一页变化，请刷新后重试。');
        }
        return [...byId.values()].filter(({id}) => !known.has(id));
    }

    async function pack(cards) {
        const bytes = new TextEncoder().encode(toPayload(cards));
        if (bytes.length > 2_000_000) return null;
        if (bytes.length <= 58_000) return new TextDecoder().decode(bytes);
        const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
        const compressed = new Uint8Array(await new Response(stream).arrayBuffer());
        if (compressed.length > 43_000) return null;
        let binary = '';
        for (let offset = 0; offset < compressed.length; offset += 8192) {
            binary += String.fromCharCode(...compressed.subarray(offset, offset + 8192));
        }
        return 'gzip:' + btoa(binary);
    }

    async function splitBatches(cards) {
        const payload = await pack(cards);
        if (payload !== null) return [{count: cards.length, payload}];
        if (cards.length === 1) throw new Error('单张详情太大，无法通过 GitHub 表单提交。');
        const middle = Math.ceil(cards.length / 2);
        return [...await splitBatches(cards.slice(0, middle)), ...await splitBatches(cards.slice(middle))];
    }

    const panelId = 'lorcanaplayer-bulk-import';
    const existingPanel = document.getElementById(panelId);
    if (existingPanel) {
        if (!existingPanel.open) existingPanel.showModal();
        return;
    }

    function element(tag, label, parent) {
        const node = document.createElement(tag);
        node.textContent = label;
        parent.append(node);
        return node;
    }

    const dialog = element('dialog', '', document.body);
    dialog.id = panelId;
    Object.assign(dialog.style, {
        width: 'min(740px, 90vw)', maxHeight: '85vh', padding: '20px', color: '#18181b',
        background: '#fff', font: '15px system-ui', overflow: 'auto',
    });
    element('h2', 'LorcanaPlayer 批量核查', dialog);
    const close = element('button', '关闭面板', dialog);
    close.onclick = () => dialog.close();
    element('p', '粘贴 GitHub export_ids 的 JSON，或者选择解压后的 existing-ids.json。', dialog);
    const input = element('textarea', '', dialog);
    input.rows = 3;
    input.style.width = '100%';
    input.setAttribute('aria-label', '表内页面 ID');
    const file = element('input', '', dialog);
    file.type = 'file';
    file.accept = '.json,application/json';
    file.setAttribute('aria-label', '选择现有 ID 文件');
    file.onchange = async () => {
        if (file.files[0]) input.value = await file.files[0].text();
    };
    const start = element('button', '检查缺卡并批量取数', dialog);
    const stop = element('button', '停止', dialog);
    stop.disabled = true;
    const status = element('p', '等待表内 ID。', dialog);
    status.setAttribute('role', 'status');
    const results = element('div', '', dialog);

    function report(message) {
        status.textContent = message;
    }

    function renderBatch({count, payload}, index) {
        const row = element('p', '批次 ' + (index + 1) + '（' + count + ' 张） ', results);
        const button = element('button', '复制到剪贴板', row);
        const box = element('textarea', '', results);
        box.value = payload;
        box.readOnly = true;
        box.style.width = '100%';
        box.rows = 2;
        box.setAttribute('aria-label', '批次 ' + (index + 1) + ' 内容');
        button.onclick = async () => {
            try {
                await navigator.clipboard.writeText(payload);
                button.textContent = '已复制，去 GitHub 粘贴';
            } catch {
                box.focus();
                box.select();
                button.textContent = '请按 Ctrl+C 复制选中的内容';
            }
        };
    }

    start.onclick = async () => {
        const controller = new AbortController();
        stop.onclick = () => controller.abort();
        dialog.addEventListener('close', stop.onclick, {once: true});
        results.replaceChildren();
        start.disabled = true;
        stop.disabled = false;
        let missing;
        const captured = [];
        try {
            const incoming = JSON.parse(input.value.trim());
            if (incoming?.version !== 1 || !Array.isArray(incoming.ids)
                || !incoming.ids.every((id) => typeof id === 'string' && /^[1-9][0-9]*$/.test(id))
                || new Set(incoming.ids).size !== incoming.ids.length) {
                throw new Error('请使用 GitHub 导出的表内 ID JSON。');
            }
            missing = await findMissing(new Set(incoming.ids), controller.signal, report);
            if (!missing.length) {
                report('目录检查完成，没有缺卡。');
                return;
            }
            const details = element('details', '', results);
            element('summary', '缺卡列表（' + missing.length + ' 张）', details);
            const list = element('ul', '', details);
            for (const card of missing) {
                const link = element('a', card.id + ' ' + card.title, element('li', '', list));
                link.href = card.url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
            }

            for (const {id, url} of missing) {
                report('发现 ' + missing.length + ' 张缺卡。已取详情 ' + captured.length + ' 张。');
                const card = readCard(await getPage(url, controller.signal), url);
                if (card.id !== id) throw new Error('详情页 ID 与目录不一致：' + id);
                captured.push(card);
            }
            report('完成：找到并取到 ' + captured.length + ' 张缺卡。');
        } catch (error) {
            report((controller.signal.aborted ? '已停止。' : String(error.message || error))
                + (missing ? ' 已取到 ' + captured.length + ' / ' + missing.length + ' 张。' : ''));
        } finally {
            try {
                if (captured.length) (await splitBatches(captured)).forEach(renderBatch);
            } catch (error) {
                report('打包失败：' + String(error.message || error));
            }
            dialog.removeEventListener('close', stop.onclick);
            stop.onclick = null;
            stop.disabled = true;
            start.disabled = false;
        }
    };
    dialog.showModal();
})();
