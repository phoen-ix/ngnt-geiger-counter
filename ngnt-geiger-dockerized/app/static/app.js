// Behaviour the pages used to carry in inline handlers; the Content-Security-Policy
// (app.py) allows no inline script.

// <form data-confirm="question">: ask before it is sent.
document.addEventListener('submit', (event) => {
    const question = event.target.dataset.confirm;
    if (question && !confirm(question)) event.preventDefault();
});

// <select data-autosubmit>: choosing an option sends its form.
document.addEventListener('change', (event) => {
    if ('autosubmit' in event.target.dataset) event.target.form.submit();
});

// Back to a page whose select sent it away: the browser shows the option picked then, not the
// one the page was served with, and picking it again would change nothing.
window.addEventListener('pageshow', () => {
    for (const select of document.querySelectorAll('select[data-autosubmit]')) select.form.reset();
});
