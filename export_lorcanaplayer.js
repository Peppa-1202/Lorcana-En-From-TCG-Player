(() => {
    const storageKey = 'lorcanaplayer-import-v1';
    if (location.origin !== 'https://lorcanaplayer.com' || !/^\/card\/[^/]+\/?$/.test(location.pathname)) {
        throw new Error('请先打开 LorcanaPlayer 的单张卡牌详情页。');
    }
    const article = document.querySelector('main article.type-card');
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
    const imageUrl = image?.closest('a[href]')?.href || image?.src || '';
    const dateParts = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date());
    const { year, month, day } = Object.fromEntries(dateParts.map(({type, value}) => [type, value]));
    const card = {
        id, title, fields, image_url: imageUrl,
        url: location.origin + location.pathname,
        captured_on: [year, month, day].join('-'),
    };
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
})();
