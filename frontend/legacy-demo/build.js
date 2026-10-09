const fs = require('fs');
const path = require('path');

const src = path.join(__dirname, 'index.html');
if (!fs.existsSync(src)) {
  console.error('frontend/index.html missing');
  process.exit(1);
}
const html = fs.readFileSync(src, 'utf8');
for (const needle of ['Autonomous Handset Fraud', '/api/v1/demo/generate', '/api/v1/score']) {
  if (!html.includes(needle)) {
    console.error('frontend/index.html is missing required content: ' + needle);
    process.exit(1);
  }
}
const dist = path.join(__dirname, 'dist');
fs.mkdirSync(dist, { recursive: true });
fs.copyFileSync(src, path.join(dist, 'index.html'));
console.log('frontend build ok -> dist/index.html');
