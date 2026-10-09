const http = require('http');
const fs = require('fs');
const path = require('path');

const index = path.join(__dirname, 'index.html');
if (!fs.existsSync(index)) {
  console.error('frontend/index.html missing');
  process.exit(1);
}

const port = Number(process.env.FRONTEND_PORT || 4173);
const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(fs.readFileSync(index));
});
server.listen(port, '127.0.0.1', () => {
  console.log(`frontend listening on http://127.0.0.1:${port}`);
});
