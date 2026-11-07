const gestureEffectNames = {
    wave: 'Волна', fire: 'Огонь', water: 'Вода', lightning: 'Молния',
    rainbow: 'Радуга', color: 'Сплошной цвет', fingers: 'Состояние пальцев',
};
const handSvgNamespace = 'http://www.w3.org/2000/svg';
const fingerSectors = [
    { name: 'Большой', x: 33, y: 118, open: 'M52 143L22 119Q12 111 19 104Q26 98 36 107L61 127Z', bent: 'M52 143L27 129Q21 124 27 114Q35 106 44 115L61 127Z' },
    { name: 'Указательный', x: 61, y: 86, open: 'M51 107L49 43Q49 32 60 32Q71 32 71 43L72 107Z', bent: 'M51 107L51 80Q51 70 61 70Q71 70 72 80L72 107Z' },
    { name: 'Средний', x: 86, y: 82, open: 'M75 105L75 29Q75 17 86 17Q97 17 97 29L97 105Z', bent: 'M75 105L75 76Q75 66 86 66Q97 66 97 76L97 105Z' },
    { name: 'Безымянный', x: 111, y: 87, open: 'M100 106L101 39Q101 28 111 28Q122 28 122 39L122 106Z', bent: 'M100 106L100 81Q100 71 111 71Q122 71 122 81L122 106Z' },
    { name: 'Мизинец', x: 135, y: 94, open: 'M125 112L126 59Q126 49 136 49Q146 49 146 59L145 112Z', bent: 'M125 112L125 89Q125 79 135 79Q145 79 145 89L145 112Z' },
];

let gestureRows = [];
let gestureListRequest = 0;
let gestureDraft = null;
let deletionTarget = null;
let gestureCapture = null;

function gestureButton(label, action, danger = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'gesture-icon' + (danger ? ' gesture-icon-danger' : '');
    button.setAttribute('aria-label', label);
    button.title = label;
    const svg = handSvg('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
    const path = danger
        ? 'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7'
        : 'M10 3h4l.6 2.6 1.9 1.1 2.5-.8 2 3.5-1.9 1.8v2.2l1.9 1.8-2 3.5-2.5-.8-1.9 1.1L14 21h-4l-.6-2.6-1.9-1.1-2.5.8-2-3.5 1.9-1.8v-2.2L3 8.8l2-3.5 2.5.8 1.9-1.1L10 3Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z';
    svg.append(handSvg('path', { d: path, fill: 'none', stroke: 'currentColor',
        'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    button.append(svg);
    button.addEventListener('click', action);
    return button;
}

function updateGestureList(rows) {
    ++gestureListRequest;
    if (JSON.stringify(rows) === JSON.stringify(gestureRows) && $('gestureList').childNodes.length) return;
    gestureRows = rows;
    const list = $('gestureList');
    list.replaceChildren();
    if (!rows.length) {
        const empty = document.createElement('span');
        empty.className = 'empty-note';
        empty.textContent = 'Комбинаций пока нет';
        list.append(empty);
    }
    for (const row of rows) {
        const item = document.createElement('div');
        item.className = 'gesture-item';
        const text = document.createElement('div');
        text.className = 'gesture-text';
        const name = document.createElement('b');
        name.textContent = row.name;
        const effect = document.createElement('span');
        effect.className = 'eff';
        effect.textContent = gestureEffectNames[row.color ? 'color' : row.effect || 'fingers'];
        text.append(name, effect);
        const actions = document.createElement('div');
        actions.className = 'gesture-actions';
        actions.append(
            gestureButton('Редактировать «' + row.name + '»', () => openGestureEditor(row)),
            gestureButton('Удалить «' + row.name + '»', () => openGestureDeletion(row), true),
        );
        item.append(text, actions);
        list.append(item);
    }
}

async function refreshCombinations() {
    const id = ++gestureListRequest;
    try {
        const response = await fetch('/api/gestures', { cache: 'no-store' });
        const data = await response.json();
        if (id !== gestureListRequest) return;
        if (!response.ok) throw new Error(data.error);
        $('gestureListError').textContent = '';
        updateGestureList(data.combinations);
    } catch {
        if (id === gestureListRequest) $('gestureListError').textContent = 'Не удалось загрузить комбинации';
    }
}

function handSvg(tag, attrs) {
    const element = document.createElementNS(handSvgNamespace, tag);
    for (const [name, value] of Object.entries(attrs)) element.setAttribute(name, value);
    return element;
}

function renderHands() {
    const diagram = $('gestureHands');
    diagram.replaceChildren();
    gestureDraft.state.forEach((state, handIndex) => {
        const panel = document.createElement('div');
        panel.className = 'gesture-hand';
        const label = document.createElement('label');
        const present = document.createElement('input');
        present.type = 'checkbox';
        present.checked = state !== null;
        present.addEventListener('change', () => {
            gestureDraft.state[handIndex] = present.checked ? gestureDraft.hiddenHands[handIndex].slice() : null;
            renderHands();
            $('gestureHands').querySelectorAll('input')[handIndex].focus();
        });
        label.append(present, ' Ладонь ' + (handIndex + 1));
        panel.append(label);
        const svg = handSvg('svg', { viewBox: '0 0 180 180', role: 'group', 'aria-label': 'Пальцы ладони ' + (handIndex + 1) });
        const mirror = handIndex === 1 ? 'translate(180 0) scale(-1 1)' : '';
        svg.append(handSvg('path', {
            d: 'M49 103H145V130Q145 149 126 157L124 175H69L66 156Q52 147 49 127Z',
            class: 'hand-palm', transform: mirror,
        }));
        fingerSectors.forEach((finger, index) => {
            const sector = handSvg('g', { class: 'finger-sector' });
            const shape = handSvg('path', { transform: mirror });
            const number = handSvg('text', { x: handIndex ? 180 - finger.x : finger.x, y: finger.y });
            number.textContent = index + 1;
            function update() {
                const extended = state && state[index];
                shape.setAttribute('d', extended ? finger.open : finger.bent);
                sector.classList.toggle('finger-open', Boolean(extended));
                if (state) {
                    sector.setAttribute('aria-pressed', String(extended));
                    sector.setAttribute('aria-label', (index + 1) + '. ' + finger.name +
                        ' палец, ' + (extended ? 'разогнут' : 'согнут'));
                }
            }
            if (state) {
                sector.setAttribute('role', 'button');
                sector.setAttribute('tabindex', '0');
                const toggle = () => {
                    state[index] = !state[index];
                    gestureDraft.hiddenHands[handIndex] = state.slice();
                    update();
                };
                sector.addEventListener('click', toggle);
                sector.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        toggle();
                    }
                });
            } else {
                sector.classList.add('finger-absent');
            }
            sector.append(shape, number);
            update();
            svg.append(sector);
        });
        panel.append(svg);
        diagram.append(panel);
    });
}

function openGestureEditor(row, capturedState) {
    const state = (row ? row.state : capturedState).map(hand => hand === null ? null : hand.slice());
    gestureDraft = {
        original: row ? row.name : null,
        revision: row ? row.revision : null,
        state,
        hiddenHands: state.map(hand => hand ? hand.slice() : [false, false, false, false, false]),
    };
    $('gestureEditorTitle').textContent = row ? 'Редактирование комбинации' : 'Новая комбинация';
    $('gestureName').value = row ? row.name : '';
    $('gestureEffect').value = row ? (row.color ? 'color' : row.effect || 'fingers') : 'wave';
    $('gestureColor').value = row && row.color ?
        '#' + row.color.map(value => value.toString(16).padStart(2, '0')).join('') : '#00ff88';
    $('gestureEditorError').textContent = '';
    $('gestureColorField').hidden = $('gestureEffect').value !== 'color';
    renderHands();
    $('gestureEditor').showModal();
    $('gestureName').focus();
}

$('gestureEffect').addEventListener('change', () => {
    $('gestureColorField').hidden = $('gestureEffect').value !== 'color';
});
$('gestureEditorCancel').addEventListener('click', () => $('gestureEditor').close());
$('gestureEditor').addEventListener('cancel', event => {
    if ($('gestureSave').disabled) event.preventDefault();
});
$('gestureForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = $('gestureSave');
    button.disabled = true;
    $('gestureEditorCancel').disabled = true;
    $('gestureEditorError').textContent = '';
    try {
        const selection = $('gestureEffect').value;
        const color = selection === 'color' ? Object.values(hexToRgb($('gestureColor').value)) : null;
        const data = await api('/api/gestures/save', {
            name: $('gestureName').value,
            original: gestureDraft.original, revision: gestureDraft.revision,
            state: gestureDraft.state,
            effect: selection === 'color' || selection === 'fingers' ? null : selection,
            color,
        });
        updateGestureList(data.combinations);
        $('gestureEditor').close();
        toast('Комбинация сохранена');
    } catch (error) {
        $('gestureEditorError').textContent = error.message;
        refreshCombinations();
    } finally {
        button.disabled = false;
        $('gestureEditorCancel').disabled = false;
    }
});

function openGestureDeletion(row) {
    deletionTarget = row;
    $('gestureDeleteName').textContent = row.name;
    $('gestureDeleteError').textContent = '';
    $('gestureDeleteDialog').showModal();
    $('gestureDeleteCancel').focus();
}
$('gestureDeleteCancel').addEventListener('click', () => $('gestureDeleteDialog').close());
$('gestureDeleteDialog').addEventListener('cancel', event => {
    if ($('gestureDeleteConfirm').disabled) event.preventDefault();
});
$('gestureDeleteForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = $('gestureDeleteConfirm');
    button.disabled = true;
    $('gestureDeleteCancel').disabled = true;
    $('gestureDeleteError').textContent = '';
    try {
        const data = await api('/api/gestures/delete', {
            name: deletionTarget.name, revision: deletionTarget.revision,
        });
        updateGestureList(data.combinations);
        $('gestureDeleteDialog').close();
        toast('Комбинация удалена');
    } catch (error) {
        $('gestureDeleteError').textContent = error.message;
        refreshCombinations();
    } finally {
        button.disabled = false;
        $('gestureDeleteCancel').disabled = false;
    }
});

function finishGestureCapture() {
    const capture = gestureCapture;
    gestureCapture = null;
    $('gestureAdd').disabled = false;
    $('gestureRecordCancel').hidden = true;
    $('gestureRecordStatus').textContent = '';
    return capture;
}

async function pollGestureCapture(capture) {
    if (gestureCapture !== capture) return;
    try {
        const result = await api('/api/gestures/record/status');
        if (gestureCapture !== capture) return;
        if (result.status === 'warming' || result.status === 'recording') {
            $('gestureRecordStatus').textContent = result.status === 'warming' ? 'Камера запускается…' :
                'Удерживайте жест · ' + Math.ceil(result.remaining) + ' с';
            setTimeout(() => pollGestureCapture(capture), 250);
            return;
        }
        finishGestureCapture();
        if (result.status === 'done') openGestureEditor(null, result.state);
        else if (result.error) $('gestureRecordStatus').textContent = result.error;
        refreshStatus();
    } catch (error) {
        if (gestureCapture !== capture) return;
        finishGestureCapture();
        $('gestureRecordStatus').textContent = error.message;
    }
}

$('gestureAdd').addEventListener('click', async () => {
    const capture = { started: api('/api/gestures/record/start') };
    gestureCapture = capture;
    $('gestureAdd').disabled = true;
    $('gestureRecordCancel').hidden = false;
    $('gestureRecordStatus').textContent = 'Камера запускается…';
    try {
        await capture.started;
        if (gestureCapture !== capture) return;
        if (!camBox.classList.contains('active')) showCameraPreview();
        $('stopCameraBtn').disabled = false;
        pollGestureCapture(capture);
    } catch (error) {
        if (gestureCapture !== capture) return;
        finishGestureCapture();
        $('gestureRecordStatus').textContent = error.message;
    }
});
$('gestureRecordCancel').addEventListener('click', async () => {
    const capture = finishGestureCapture();
    $('gestureAdd').disabled = true;
    try {
        if (capture) await capture.started;
        await api('/api/gestures/record/cancel');
    } catch { /* API shows the error */ }
    finally {
        $('gestureAdd').disabled = false;
        refreshStatus();
    }
});

refreshCombinations();
setInterval(refreshCombinations, 5000);
