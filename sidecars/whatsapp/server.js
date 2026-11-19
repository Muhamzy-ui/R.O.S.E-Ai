/**
 * R.O.S.E (Project Mirror) - WhatsApp Baileys Sidecar
 * Lightweight Node.js WebSocket Bridge for Verified WhatsApp Automation
 * Rules A2 (No fake success), A3 (Strict local API), A5 (Localhost only)
 */

const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');
const QRCode = require('qrcode');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    proto
} = require('@whiskeysockets/baileys');
const pino = require('pino');

// Paths & Configuration
const ROOT_DIR = path.resolve(__dirname, '../..');
const AUTH_DIR = path.resolve(ROOT_DIR, 'whatsapp_auth');
const DB_PATH = path.resolve(AUTH_DIR, 'whatsapp_cache.db');
const PORT = parseInt(process.env.ROSE_WHATSAPP_PORT || '48201', 10);
const AUTH_TOKEN = process.env.ROSE_WHATSAPP_TOKEN || 'rose_wa_secret_token_default';

if (!fs.existsSync(AUTH_DIR)) {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
}

// ── SQLite Local Cache ────────────────────────────────────────────────────────
const db = new DatabaseSync(DB_PATH);
db.exec(`
    CREATE TABLE IF NOT EXISTS contacts (
        jid TEXT PRIMARY KEY,
        name TEXT,
        push_name TEXT,
        phone TEXT,
        updated_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS chats (
        jid TEXT PRIMARY KEY,
        name TEXT,
        unread_count INTEGER DEFAULT 0,
        last_message TEXT,
        last_message_ts INTEGER,
        is_group INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        chat_jid TEXT,
        sender_jid TEXT,
        text TEXT,
        status TEXT,
        from_me INTEGER,
        timestamp INTEGER
    );
`);

const stmtUpsertContact = db.prepare(`
    INSERT INTO contacts (jid, name, push_name, phone, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(jid) DO UPDATE SET
        name = COALESCE(excluded.name, contacts.name),
        push_name = COALESCE(excluded.push_name, contacts.push_name),
        phone = COALESCE(excluded.phone, contacts.phone),
        updated_at = excluded.updated_at
`);

const stmtUpsertChat = db.prepare(`
    INSERT INTO chats (jid, name, unread_count, last_message, last_message_ts, is_group)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(jid) DO UPDATE SET
        name = COALESCE(excluded.name, chats.name),
        unread_count = COALESCE(excluded.unread_count, chats.unread_count),
        last_message = COALESCE(excluded.last_message, chats.last_message),
        last_message_ts = COALESCE(excluded.last_message_ts, chats.last_message_ts)
`);

const stmtInsertMessage = db.prepare(`
    INSERT OR REPLACE INTO messages (id, chat_jid, sender_jid, text, status, from_me, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?)
`);

const stmtUpsertIncomingChat = db.prepare(`
    INSERT INTO chats (jid, name, unread_count, last_message, last_message_ts, is_group)
    VALUES (?, NULL, 1, ?, ?, ?)
    ON CONFLICT(jid) DO UPDATE SET
        unread_count = COALESCE(chats.unread_count, 0) + 1,
        last_message = excluded.last_message,
        last_message_ts = excluded.last_message_ts
`);

// ── State Management ──────────────────────────────────────────────────────────
let sock = null;
let isConnected = false;
let isPaired = false;
let currentQR = null;
let currentQRDataUrl = null;
let userInfo = null;

// Map of pending message ACK resolvers: messageId -> { resolve, reject, timer }
const pendingAcks = new Map();

// ── Baileys Client Initialization ─────────────────────────────────────────────
async function startBaileys() {
    const logger = pino({ level: 'silent' });
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version, isLatest } = await fetchLatestBaileysVersion().catch(() => ({ version: [2, 3000, 1015901307], isLatest: true }));

    sock = makeWASocket({
        version,
        logger,
        printQRInTerminal: false,
        auth: state,
        browser: ['R.O.S.E Desktop', 'Chrome', '1.0.0'],
        syncFullHistory: false,
        generateHighQualityLinkPreview: false,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            currentQR = qr;
            isPaired = false;
            isConnected = false;
            try {
                currentQRDataUrl = await QRCode.toDataURL(qr);
            } catch (err) {
                currentQRDataUrl = null;
            }
            console.log('\n[ROSE WhatsApp] 📱 New QR code generated. Scan with WhatsApp (Linked Devices):');
            try {
                const asciiQR = await QRCode.toString(qr, { type: 'terminal', small: true });
                console.log(asciiQR);
            } catch (e) {}
        }

        if (connection === 'close') {
            isConnected = false;
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            console.log(`[ROSE WhatsApp] ⚠️ Connection closed (status: ${statusCode}). Reconnect: ${shouldReconnect}`);

            if (statusCode === DisconnectReason.loggedOut) {
                isPaired = false;
                currentQR = null;
                currentQRDataUrl = null;
                userInfo = null;
                // Clear session credentials
                try {
                    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                    fs.mkdirSync(AUTH_DIR, { recursive: true });
                } catch (e) {}
            }

            if (shouldReconnect) {
                setTimeout(startBaileys, 3000);
            }
        } else if (connection === 'open') {
            isConnected = true;
            isPaired = true;
            currentQR = null;
            currentQRDataUrl = null;
            userInfo = sock.user;
            console.log(`[ROSE WhatsApp] ✅ Connected to WhatsApp as: ${sock.user?.id || 'Unknown'}`);
        }
    });

    // ── Contacts Update ───────────────────────────────────────────────────────
    sock.ev.on('contacts.upsert', (contacts) => {
        const now = Date.now();
        for (const c of contacts) {
            const jid = c.id;
            const name = c.name || null;
            const pushName = c.notify || null;
            const phone = jid.split('@')[0];
            try {
                stmtUpsertContact.run(jid, name, pushName, phone, now);
            } catch (e) {}
        }
    });

    sock.ev.on('contacts.update', (updates) => {
        const now = Date.now();
        for (const u of updates) {
            const jid = u.id;
            const name = u.name || null;
            const pushName = u.notify || null;
            const phone = jid ? jid.split('@')[0] : null;
            try {
                stmtUpsertContact.run(jid, name, pushName, phone, now);
            } catch (e) {}
        }
    });

    // ── Chats Update ──────────────────────────────────────────────────────────
    sock.ev.on('chats.upsert', (chats) => {
        for (const c of chats) {
            const jid = c.id;
            const name = c.name || null;
            const unread = c.unreadCount || 0;
            const isGroup = jid.endsWith('@g.us') ? 1 : 0;
            try {
                stmtUpsertChat.run(jid, name, unread, null, null, isGroup);
            } catch (e) {}
        }
    });

    // ── Initial History Sync (contacts + chats right after QR link) ──────────
    sock.ev.on('messaging-history.set', ({ chats = [], contacts = [], messages = [] }) => {
        const now = Date.now();
        for (const ct of contacts) {
            const jid = ct.id;
            if (!jid) continue;
            const name = ct.name || ct.verifiedName || null;
            const pushName = ct.notify || null;
            const phone = jid.includes('@') ? jid.split('@')[0] : null;
            try {
                stmtUpsertContact.run(jid, name, pushName, phone, now);
            } catch (e) {}
        }
        for (const c of chats) {
            const jid = c.id;
            if (!jid) continue;
            const name = c.name || null;
            const unread = typeof c.unreadCount === 'number' ? c.unreadCount : 0;
            const isGroup = jid.endsWith('@g.us') ? 1 : 0;
            const lastTs = c.conversationTimestamp ? Number(c.conversationTimestamp) * 1000 : null;
            try {
                stmtUpsertChat.run(jid, name, unread, null, lastTs, isGroup);
            } catch (e) {}
        }
        for (const m of messages) {
            const id = m.key?.id;
            const chatJid = m.key?.remoteJid;
            if (!id || !chatJid) continue;
            const senderJid = m.key?.participant || chatJid;
            const fromMe = m.key?.fromMe ? 1 : 0;
            const text = m.message?.conversation || m.message?.extendedTextMessage?.text || '';
            const status = m.status ? String(m.status) : (fromMe ? 'SENT' : 'RECEIVED');
            const msgTs = m.messageTimestamp ? Number(m.messageTimestamp) * 1000 : now;
            try {
                stmtInsertMessage.run(id, chatJid, senderJid, text, status, fromMe, msgTs);
            } catch (e) {}
        }
    });

    // ── Incoming Messages & Server Receipts (Rule A2: Real Verification) ──────
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        const now = Date.now();
        for (const m of messages) {
            const id = m.key?.id;
            const chatJid = m.key?.remoteJid;
            const senderJid = m.key?.participant || chatJid;
            const fromMe = m.key?.fromMe ? 1 : 0;
            const text = m.message?.conversation || m.message?.extendedTextMessage?.text || '';
            const status = m.status ? String(m.status) : (fromMe ? 'SENT' : 'RECEIVED');

            if (id && chatJid) {
                try {
                    stmtInsertMessage.run(id, chatJid, senderJid, text, status, fromMe, now);
                } catch (e) {}

                // Update chat unread count if message from other
                if (!fromMe) {
                    const isGroup = chatJid.endsWith('@g.us') ? 1 : 0;
                    try {
                        stmtUpsertIncomingChat.run(chatJid, text, now, isGroup);
                    } catch (e) {}
                }
            }

            // Check if this outgoing message received server acknowledgment
            if (fromMe && id && pendingAcks.has(id)) {
                // In Baileys, status 2 corresponds to SERVER_ACK (sent to server)
                const pending = pendingAcks.get(id);
                clearTimeout(pending.timer);
                pendingAcks.delete(id);
                pending.resolve({
                    ok: true,
                    verified: true,
                    messageId: id,
                    timestamp: now,
                    chat: chatJid
                });
            }
        }
    });

    // Message status update (receipt / delivery / read ack)
    sock.ev.on('messages.update', (updates) => {
        for (const u of updates) {
            const id = u.key?.id;
            const status = u.update?.status;
            if (id && pendingAcks.has(id)) {
                // status >= 2 means message was received by WhatsApp servers (SERVER_ACK)
                if (status >= 2 || status === proto.WebMessageInfo.Status.SERVER_ACK || status === proto.WebMessageInfo.Status.DELIVERY_ACK) {
                    const pending = pendingAcks.get(id);
                    clearTimeout(pending.timer);
                    pendingAcks.delete(id);
                    pending.resolve({
                        ok: true,
                        verified: true,
                        messageId: id,
                        status: String(status)
                    });
                }
            }
        }
    });
}

// ── HTTP API Server (Bound strictly to 127.0.0.1) ──────────────────────────────
const server = http.createServer(async (req, res) => {
    // Only accept connections from localhost
    const remoteIp = req.socket.remoteAddress;
    if (remoteIp !== '127.0.0.1' && remoteIp !== '::1' && remoteIp !== '::ffff:127.0.0.1') {
        res.writeHead(403, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Access denied: Localhost only' }));
        return;
    }

    const url = new URL(req.url, `http://${req.headers.host}`);
    const pathname = url.pathname;
    const method = req.method.toUpperCase();

    // Security Token check
    const authHeader = req.headers['authorization'] || '';
    const tokenQuery = url.searchParams.get('token') || '';
    const providedToken = authHeader.replace(/^Bearer\s+/i, '') || tokenQuery;

    if (AUTH_TOKEN && providedToken !== AUTH_TOKEN) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Unauthorized: Invalid or missing token' }));
        return;
    }

    // Helper to send JSON responses
    const sendJson = (statusCode, data) => {
        res.writeHead(statusCode, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(data));
    };

    // Helper to read JSON request body
    const readBody = () => new Promise((resolve) => {
        let body = '';
        req.on('data', chunk => { body += chunk; });
        req.on('end', () => {
            try {
                resolve(body ? JSON.parse(body) : {});
            } catch (e) {
                resolve({});
            }
        });
    });

    try {
        // 1. GET /status
        if (pathname === '/status' && method === 'GET') {
            return sendJson(200, {
                ok: true,
                connected: isConnected,
                paired: isPaired,
                qr: currentQR,
                qrDataUrl: currentQRDataUrl,
                user: userInfo,
                authDir: AUTH_DIR
            });
        }

        // 2. GET /chats?query=
        if (pathname === '/chats' && method === 'GET') {
            const q = (url.searchParams.get('query') || '').trim();
            let querySql = `
                SELECT c.jid, c.name, c.push_name, c.phone, ch.unread_count, ch.last_message, ch.last_message_ts
                FROM contacts c
                LEFT JOIN chats ch ON c.jid = ch.jid
            `;
            if (q) {
                const escaped = q.replace(/'/g, "''");
                querySql += ` WHERE c.name LIKE '%${escaped}%' OR c.push_name LIKE '%${escaped}%' OR c.phone LIKE '%${escaped}%'`;
            }
            querySql += ` ORDER BY ch.last_message_ts DESC LIMIT 50`;

            const rows = db.prepare(querySql).all();
            return sendJson(200, { ok: true, count: rows.length, chats: rows });
        }

        // 3. POST /on-whatsapp
        if (pathname === '/on-whatsapp' && method === 'POST') {
            const body = await readBody();
            let phone = String(body.phone || '').replace(/\D+/g, '');
            if (!phone) {
                return sendJson(400, { ok: false, error: 'Phone number required' });
            }
            if (!isConnected || !sock) {
                return sendJson(503, { ok: false, error: 'WhatsApp is not connected yet' });
            }

            try {
                const [result] = await sock.onWhatsApp(phone);
                return sendJson(200, {
                    ok: true,
                    exists: Boolean(result?.exists),
                    jid: result?.jid || null
                });
            } catch (err) {
                return sendJson(500, { ok: false, error: `onWhatsApp error: ${err.message}` });
            }
        }

        // 4. POST /send (Rule A2: Wait for Server ACK)
        if (pathname === '/send' && method === 'POST') {
            const body = await readBody();
            const to = String(body.to || '').trim();
            const message = String(body.message || '').trim();

            if (!to || !message) {
                return sendJson(400, { ok: false, error: "Both 'to' and 'message' fields are required" });
            }
            if (!isConnected || !sock) {
                return sendJson(503, { ok: false, error: 'WhatsApp is not connected. Scan QR code first.' });
            }

            let jid = to;
            if (!jid.includes('@')) {
                const cleanPhone = jid.replace(/\D+/g, '');
                jid = `${cleanPhone}@s.whatsapp.net`;
            }

            try {
                // Send message through Baileys
                const sentMsg = await sock.sendMessage(jid, { text: message });
                const messageId = sentMsg?.key?.id;

                if (!messageId) {
                    return sendJson(500, { ok: false, verified: false, error: 'Failed to generate message key' });
                }

                // Wait up to 10 seconds for real server ACK
                const ackPromise = new Promise((resolve) => {
                    const timer = setTimeout(() => {
                        pendingAcks.delete(messageId);
                        resolve({
                            ok: false,
                            verified: false,
                            error: "Message sent to queue but server hasn't acknowledged it yet"
                        });
                    }, 10000);

                    pendingAcks.set(messageId, { resolve, timer });
                });

                const ackResult = await ackPromise;
                return sendJson(ackResult.ok ? 200 : 504, ackResult);
            } catch (err) {
                return sendJson(500, { ok: false, verified: false, error: err.message });
            }
        }

        // 5. GET /unread
        if (pathname === '/unread' && method === 'GET') {
            const rows = db.prepare(`
                SELECT jid, name, unread_count, last_message, last_message_ts
                FROM chats
                WHERE unread_count > 0
                ORDER BY last_message_ts DESC
            `).all();

            const unreadItems = rows.map(r => ({
                chat: r.name || r.jid.split('@')[0],
                jid: r.jid,
                unreadCount: r.unread_count,
                lastMessage: r.last_message || ''
            }));

            return sendJson(200, { ok: true, count: unreadItems.length, unread: unreadItems });
        }

        // 6. POST /shutdown
        if (pathname === '/shutdown' && method === 'POST') {
            sendJson(200, { ok: true, message: 'Sidecar shutting down' });
            setTimeout(() => {
                if (sock) {
                    try { sock.end(); } catch (e) {}
                }
                process.exit(0);
            }, 500);
            return;
        }

        return sendJson(404, { error: `Endpoint ${pathname} not found` });

    } catch (e) {
        return sendJson(500, { ok: false, error: e.message });
    }
});

server.listen(PORT, '127.0.0.1', () => {
    console.log(`[ROSE WhatsApp Sidecar] 🚀 Listening on http://127.0.0.1:${PORT}`);
    startBaileys().catch(err => {
        console.error('[ROSE WhatsApp Sidecar] Fatal startup error:', err);
    });
});

process.on('SIGTERM', () => {
    if (sock) { try { sock.end(); } catch (e) {} }
    process.exit(0);
});

