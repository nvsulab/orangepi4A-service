// Use the address used to reach this OrangePi, including access through its Wi-Fi hotspot.
for (const link of document.querySelectorAll('[data-service-port], [data-home]')) {
    const url = new URL('/', window.location.href);
    url.port = link.hasAttribute('data-home') ? '57575' : link.dataset.servicePort;
    link.href = url.href;
}
