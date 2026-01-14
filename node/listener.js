/**
 * WhatsApp Fuel Extractor - Message Listener
 * 
 * Connects to WhatsApp, monitors the configured group, and saves
 * incoming messages as JSON files for the Python processor.
 * 
 * Features:
 * - Auto QR code display for authentication
 * - Session persistence (survives restarts)
 * - Config file watching (edit phone/group triggers re-setup)
 * - Robust error handling and reconnection
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const path = require('path');
const chokidar = require('chokidar');
const http = require('http');

// Try to get Chromium path from puppeteer
let chromiumPath = null;
try {
    const puppeteer = require('puppeteer');
    chromiumPath = puppeteer.executablePath();
    if (chromiumPath && fs.existsSync(chromiumPath)) {
        console.log('[CHROMIUM] Found at:', chromiumPath);
    } else {
        chromiumPath = null;
    }
} catch (err) {
    // Puppeteer not installed or Chromium not found
    console.log('[WARN] Puppeteer Chromium not found, will try system Chrome...');
}

// Fallback: Try to find system Chrome/Chromium
function findSystemChrome() {
    const possiblePaths = process.platform === 'win32' ? [
        // Windows paths
        path.join(process.env.PROGRAMFILES || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(process.env['PROGRAMFILES(X86)'] || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
        path.join(process.env.PROGRAMFILES || '', 'Chromium', 'Application', 'chrome.exe'),
        path.join(process.env.LOCALAPPDATA || '', 'Chromium', 'Application', 'chrome.exe'),
    ] : process.platform === 'darwin' ? [
        // macOS paths
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ] : [
        // Linux paths
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/snap/bin/chromium',
    ];
    
    for (const p of possiblePaths) {
        if (p && fs.existsSync(p)) {
            return p;
        }
    }
    return null;
}

// Use puppeteer's Chromium or fallback to system Chrome
if (!chromiumPath) {
    chromiumPath = findSystemChrome();
    if (chromiumPath) {
        console.log('[CHROMIUM] Using system Chrome at:', chromiumPath);
    }
}

// Paths
const ROOT_DIR = path.join(__dirname, '..');
const CONFIG_PATH = path.join(ROOT_DIR, 'config.json');
const SESSION_PATH = path.join(ROOT_DIR, 'data', 'session');
const RAW_MESSAGES_PATH = path.join(ROOT_DIR, 'data', 'raw_messages');

// Conda environment name - works cross-platform (Linux/Windows)
const CONDA_ENV = process.env.CONDA_ENV || 'fuel-extractor';

/**
 * Get the conda base path and build proper environment paths
 * Works on both Linux and Windows
 */
function getCondaPaths() {
    const homeDir = process.env.HOME || process.env.USERPROFILE;
    const isWindows = process.platform === 'win32';
    
    // Common conda installation locations
    const condaLocations = isWindows ? [
        path.join(homeDir, 'anaconda3'),
        path.join(homeDir, 'miniconda3'),
        path.join(homeDir, 'Anaconda3'),
        path.join(homeDir, 'Miniconda3'),
        'C:\\ProgramData\\anaconda3',
        'C:\\ProgramData\\miniconda3',
    ] : [
        path.join(homeDir, 'anaconda3'),
        path.join(homeDir, 'miniconda3'),
        '/opt/anaconda3',
        '/opt/miniconda3',
        '/usr/local/anaconda3',
    ];
    
    // Find conda installation
    let condaBase = null;
    for (const loc of condaLocations) {
        if (fs.existsSync(loc)) {
            condaBase = loc;
            break;
        }
    }
    
    if (!condaBase) {
        console.log('[WARN] Could not find conda installation');
        return null;
    }
    
    // Build paths to conda and python executables
    const condaBin = isWindows 
        ? path.join(condaBase, 'Scripts', 'conda.exe')
        : path.join(condaBase, 'bin', 'conda');
    
    const envPython = isWindows
        ? path.join(condaBase, 'envs', CONDA_ENV, 'python.exe')
        : path.join(condaBase, 'envs', CONDA_ENV, 'bin', 'python');
    
    return { condaBase, condaBin, envPython };
}

/**
 * Build a cross-platform command to run Python scripts
 * Uses direct path to Python in conda environment
 */
function buildPythonCommand(scriptPath, ...args) {
    const paths = getCondaPaths();
    const argsStr = args.join(' ');
    
    if (paths && fs.existsSync(paths.envPython)) {
        // Use direct path to Python executable (most reliable)
        return `"${paths.envPython}" "${scriptPath}" ${argsStr}`.trim();
    }
    
    // Fallback: try conda run with full path
    if (paths && fs.existsSync(paths.condaBin)) {
        return `"${paths.condaBin}" run -n ${CONDA_ENV} --no-capture-output python "${scriptPath}" ${argsStr}`.trim();
    }
    
    // Last resort: assume conda is in PATH (may not work in all shells)
    console.log('[WARN] Using fallback conda command - may not work if conda not in PATH');
    return `conda run -n ${CONDA_ENV} --no-capture-output python "${scriptPath}" ${argsStr}`.trim();
}

// State
let config = null;
let client = null;
let isReady = false;
let targetGroupId = null;

/**
 * Load configuration from config.json
 */
function loadConfig() {
    try {
        const rawConfig = fs.readFileSync(CONFIG_PATH, 'utf8');
        return JSON.parse(rawConfig);
    } catch (error) {
        console.error('[ERROR] Error loading config.json:', error.message);
        console.log('\n[INFO] Please ensure config.json exists with the following structure:';
        console.log(JSON.stringify({
            whatsapp: { phoneNumber: "your-phone", groupName: "Your Group Name" },
            output: { excelFolder: "./data/output", excelFileName: "fuel_records.xlsx" }
        }, null, 2));
        process.exit(1);
    }
}

/**
 * Validate configuration
 */
function validateConfig(cfg) {
    const issues = [];
    
    if (!cfg.whatsapp) {
        issues.push('Missing "whatsapp" section');
    } else {
        if (!cfg.whatsapp.groupName || cfg.whatsapp.groupName.trim() === '') {
            issues.push('Missing or empty "whatsapp.groupName"');
        }
    }
    
    return issues;
}

/**
 * Clear session to force new QR authentication
 */
function clearSession() {
    console.log('[INFO] Clearing existing session for new setup...');
    try {
        if (fs.existsSync(SESSION_PATH)) {
            fs.rmSync(SESSION_PATH, { recursive: true, force: true });
            fs.mkdirSync(SESSION_PATH, { recursive: true });
        }
    } catch (error) {
        console.error('Warning: Could not clear session:', error.message);
    }
}

// Track last processed message timestamp for catching up on missed messages
const LAST_PROCESSED_FILE = path.join(ROOT_DIR, 'data', 'last_processed.json');

/**
 * Get the timestamp of the last processed message
 */
function getLastProcessedTime() {
    try {
        if (fs.existsSync(LAST_PROCESSED_FILE)) {
            const data = JSON.parse(fs.readFileSync(LAST_PROCESSED_FILE, 'utf8'));
            return data.timestamp || 0;
        }
    } catch (e) {}
    return 0;
}

/**
 * Update the last processed message timestamp
 */
function updateLastProcessedTime(timestamp) {
    try {
        fs.writeFileSync(LAST_PROCESSED_FILE, JSON.stringify({
            timestamp: timestamp,
            datetime: new Date(timestamp * 1000).toISOString()
        }, null, 2));
    } catch (e) {
        console.error('Warning: Could not update last processed time:', e.message);
    }
}

/**
 * Check if a message has already been processed or is pending
 */
function isMessageAlreadyProcessed(msgId) {
    // Check in raw_messages (pending)
    const rawFiles = fs.readdirSync(RAW_MESSAGES_PATH);
    for (const file of rawFiles) {
        if (!file.endsWith('.json')) continue;
        try {
            const content = JSON.parse(fs.readFileSync(path.join(RAW_MESSAGES_PATH, file), 'utf8'));
            if (content.id === msgId) return true;
        } catch (e) {}
    }
    
    // Check in processed folder
    const processedPath = path.join(ROOT_DIR, 'data', 'processed');
    if (fs.existsSync(processedPath)) {
        const processedFiles = fs.readdirSync(processedPath);
        for (const file of processedFiles) {
            if (!file.endsWith('.json')) continue;
            try {
                const content = JSON.parse(fs.readFileSync(path.join(processedPath, file), 'utf8'));
                if (content.id === msgId) return true;
            } catch (e) {}
        }
    }
    
    // Check in errors folder
    const errorsPath = path.join(ROOT_DIR, 'data', 'errors');
    if (fs.existsSync(errorsPath)) {
        const errorFiles = fs.readdirSync(errorsPath);
        for (const file of errorFiles) {
            if (!file.endsWith('.json')) continue;
            try {
                const content = JSON.parse(fs.readFileSync(path.join(errorsPath, file), 'utf8'));
                if (content.id === msgId) return true;
            } catch (e) {}
        }
    }
    
    return false;
}

/**
 * Fetch and process messages that were sent while the system was offline
 * Always checks recent messages and filters out already-processed ones
 */
async function fetchMissedMessages(chatId) {
    console.log('\n[INFO] Checking for unprocessed messages...');
    
    try {
        const chat = await client.getChatById(chatId);
        
        // Fetch messages from history (limit to last 50 to avoid too many)
        const messages = await chat.fetchMessages({ limit: 50 });
        
        let totalFuelReports = 0;
        let processedCount = 0;
        let alreadyProcessedCount = 0;
        
        // Only look at messages from the last 24 hours
        const cutoffTime = Math.floor(Date.now() / 1000) - (24 * 60 * 60);
        
        console.log(`   Scanning last 50 messages for fuel reports...`);
        
        for (const msg of messages) {
            // Skip messages older than 24 hours
            if (msg.timestamp < cutoffTime) continue;
            
            // Check if it's a fuel report
            if (!isFuelReport(msg.body)) continue;
            
            totalFuelReports++;
            
            // Check if already processed (avoid duplicates)
            if (isMessageAlreadyProcessed(msg.id._serialized)) {
                alreadyProcessedCount++;
                continue;
            }
            
            // Get sender info
            let senderName = 'Unknown';
            let senderPhone = '';
            
            try {
                if (msg.fromMe) {
                    senderName = client.info.pushname || 'Me';
                    senderPhone = client.info.wid.user;
                } else {
                    const contact = await msg.getContact();
                    senderName = contact.pushname || contact.name || contact.number || 'Unknown';
                    senderPhone = contact.number || '';
                }
            } catch (e) {
                senderName = msg.author || 'Unknown';
            }
            
            // Prepare message data
            const messageData = {
                id: msg.id._serialized,
                timestamp: msg.timestamp,
                datetime: new Date(msg.timestamp * 1000).toISOString(),
                groupName: chat.name,
                groupId: chat.id._serialized,
                senderPhone: senderPhone,
                senderName: senderName,
                body: msg.body,
                capturedAt: new Date().toISOString(),
                wasOffline: true
            };
            
            // Save message for processing
            if (saveMessage(messageData)) {
                processedCount++;
                console.log(`   [MSG] Found unprocessed: ${senderName} at ${new Date(msg.timestamp * 1000).toLocaleString()}`);
            }
        }
        
        // Show summary
        if (processedCount > 0) {
            console.log(`\n   [OK] Captured ${processedCount} new fuel report(s) for processing`);
            
            // Notify the group about missed messages being processed
            const missedMsg = `[INBOX] *PROCESSING MISSED MESSAGES*\n\n` +
                `[MSG] Found ${processedCount} unprocessed fuel report(s)\n` +
                `[WAIT] Processing now...\n\n` +
                `_You will receive confirmations or error messages shortly_`;
            await sendGroupMessage(missedMsg);
        } else if (alreadyProcessedCount > 0) {
            console.log(`   [OK] Found ${totalFuelReports} fuel report(s), all already processed`);
        } else {
            console.log('   [OK] No fuel reports found in recent messages');
        }
        
        // Update last processed time to now
        updateLastProcessedTime(Math.floor(Date.now() / 1000));
        
        return processedCount;
        
    } catch (error) {
        console.error('   [ERROR] Error fetching messages:', error.message);
        updateLastProcessedTime(Math.floor(Date.now() / 1000));
        return 0;
    }
}

/**
 * Safe JSON file operations with locking
 */
function safeReadJSON(filepath, defaultValue = null) {
    try {
        if (!fs.existsSync(filepath)) {
            return defaultValue;
        }
        const content = fs.readFileSync(filepath, 'utf8');
        if (!content.trim()) {
            return defaultValue;
        }
        return JSON.parse(content);
    } catch (error) {
        console.error(`[WARN] Error reading JSON from ${filepath}:`, error.message);
        // Create backup of corrupted file
        try {
            const backupPath = filepath + '.corrupted.' + Date.now();
            fs.copyFileSync(filepath, backupPath);
            console.log(`[BACKUP] Created backup of corrupted file: ${backupPath}`);
        } catch (e) {}
        return defaultValue;
    }
}

function safeWriteJSON(filepath, data, indent = 2) {
    const tempPath = filepath + '.tmp';
    try {
        // Ensure directory exists
        const dir = path.dirname(filepath);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        // Write to temp file first (atomic write pattern)
        fs.writeFileSync(tempPath, JSON.stringify(data, null, indent));
        // Rename temp to actual (atomic on most filesystems)
        fs.renameSync(tempPath, filepath);
        return true;
    } catch (error) {
        console.error(`[ERROR] Error writing JSON to ${filepath}:`, error.message);
        // Clean up temp file if exists
        try {
            if (fs.existsSync(tempPath)) {
                fs.unlinkSync(tempPath);
            }
        } catch (e) {}
        return false;
    }
}

/**
 * Save a message to the raw_messages folder as JSON
 */
function saveMessage(messageData) {
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const filename = `msg_${timestamp}_${Math.random().toString(36).substr(2, 9)}.json`;
    const filepath = path.join(RAW_MESSAGES_PATH, filename);
    
    // Ensure raw_messages directory exists
    if (!fs.existsSync(RAW_MESSAGES_PATH)) {
        try {
            fs.mkdirSync(RAW_MESSAGES_PATH, { recursive: true });
        } catch (error) {
            console.error('[ERROR] Error creating raw_messages directory:', error.message);
            return false;
        }
    }
    
    if (safeWriteJSON(filepath, messageData)) {
        console.log(`[SAVED] Saved message: ${filename}`);
        return true;
    } else {
        console.error('[ERROR] Error saving message');
        return false;
    }
}

/**
 * Find the target group by name
 */
async function findTargetGroup() {
    if (!isReady || !client) return null;
    
    const groupName = config.whatsapp.groupName.toLowerCase().trim();
    console.log(`[SEARCH] Searching for group: "${config.whatsapp.groupName}"`);
    
    try {
        const chats = await client.getChats();
        const groups = chats.filter(chat => chat.isGroup);
        
        console.log(`[LIST] Found ${groups.length} groups:`);
        groups.forEach((g, i) => {
            const match = g.name.toLowerCase().trim() === groupName ? ' [TARGET]' : '';
            console.log(`   ${i + 1}. ${g.name}${match}`);
        });
        
        const targetGroup = groups.find(g => 
            g.name.toLowerCase().trim() === groupName
        );
        
        if (targetGroup) {
            console.log(`\n[OK] Found target group: "${targetGroup.name}" (ID: ${targetGroup.id._serialized})`);
            return targetGroup.id._serialized;
        } else {
            console.log(`\n[WARN] Group "${config.whatsapp.groupName}" not found!`);
            console.log('   Make sure the bot phone number is a member of this group.');
            console.log('   Group names are case-insensitive but must match exactly.');
            return null;
        }
    } catch (error) {
        console.error('[ERROR] Error finding group:', error.message);
        return null;
    }
}

/**
 * Check if a message is a fuel report (must start with "FUEL UPDATE")
 */
function isFuelReport(body) {
    if (!body) return false;
    const text = body.toUpperCase().trim();
    
    // Must start with "FUEL UPDATE" keyword
    if (!text.startsWith('FUEL UPDATE')) {
        return false;
    }
    
    // Also check for at least 2 fuel-related keywords
    const keywords = ['DRIVER', 'CAR', 'LITERS', 'LITRES', 'AMOUNT', 'TYPE', 'ODOMETER', 'KSH', 'DIESEL', 'PETROL'];
    const matches = keywords.filter(kw => text.includes(kw));
    return matches.length >= 2;
}

/**
 * Check if message is an admin command
 */
function isAdminCommand(body) {
    if (!body) return false;
    const text = body.trim();
    return text.startsWith('!');
}

/**
 * Get system status for !status command
 */
function getSystemStatus() {
    const uptime = process.uptime();
    const hours = Math.floor(uptime / 3600);
    const minutes = Math.floor((uptime % 3600) / 60);
    const seconds = Math.floor(uptime % 60);
    
    const rawMsgCount = fs.readdirSync(RAW_MESSAGES_PATH).filter(f => f.endsWith('.json')).length;
    const processedPath = path.join(ROOT_DIR, 'data', 'processed');
    const errorsPath = path.join(ROOT_DIR, 'data', 'errors');
    
    let processedCount = 0;
    let errorsCount = 0;
    
    try {
        if (fs.existsSync(processedPath)) {
            processedCount = fs.readdirSync(processedPath).filter(f => f.endsWith('.json')).length;
        }
        if (fs.existsSync(errorsPath)) {
            errorsCount = fs.readdirSync(errorsPath).filter(f => f.endsWith('.json')).length;
        }
    } catch (e) {}
    
    let status = `[STATUS] *SYSTEM STATUS*\n`;
    status += `----------------------------\n\n`;
    status += `[OK] *Listener:* Running\n`;
    status += `[TIME] *Uptime:* ${hours}h ${minutes}m ${seconds}s\n`;
    status += `[GROUP] *Group:* ${config.whatsapp.groupName}\n\n`;
    status += `[QUEUE] *Message Queue:*\n`;
    status += `   Pending: ${rawMsgCount}\n`;
    status += `   Processed: ${processedCount}\n`;
    status += `   Errors: ${errorsCount}\n`;
    
    return status;
}

/**
 * Get fleet list for !list command
 */
function getFleetList() {
    const fleetFile = path.join(ROOT_DIR, 'python', 'processor.py');
    
    try {
        const content = fs.readFileSync(fleetFile, 'utf8');
        const match = content.match(/ALLOWED_PLATES\s*=\s*\{([^}]+)\}/s);
        
        if (match) {
            const plates = match[1].match(/'([A-Z0-9]+)'/g);
            if (plates) {
                const cleanPlates = plates.map(p => p.replace(/'/g, '')).sort();
                let msg = `[FLEET] *FLEET VEHICLES* (${cleanPlates.length})\n`;
                msg += `----------------------------\n\n`;
                
                // Group in columns of 3
                for (let i = 0; i < cleanPlates.length; i += 3) {
                    const row = cleanPlates.slice(i, i + 3).join('  •  ');
                    msg += `${row}\n`;
                }
                
                return msg;
            }
        }
        return '[ERROR] Could not read fleet list';
    } catch (e) {
        return '[ERROR] Error reading fleet list: ' + e.message;
    }
}

/**
 * Get pending approvals for !pending command
 */
function getPendingApprovals() {
    const pendingFile = path.join(ROOT_DIR, 'data', 'pending_approvals.json');
    
    try {
        if (!fs.existsSync(pendingFile)) {
            return '[OK] No pending approvals';
        }
        
        const approvals = JSON.parse(fs.readFileSync(pendingFile, 'utf8'));
        const pending = approvals.filter(a => a.status === 'pending');
        
        if (pending.length === 0) {
            return '[OK] No pending approvals';
        }
        
        let msg = `[PENDING] *PENDING APPROVALS* (${pending.length})\n`;
        msg += `----------------------------\n\n`;
        
        for (const approval of pending) {
            const record = approval.record || {};
            const timestamp = new Date(approval.timestamp).toLocaleString();
            
            msg += `*ID:* ${approval.id}\n`;
            msg += `*Type:* ${approval.type}\n`;
            msg += `*Car:* ${record.car || 'N/A'}\n`;
            msg += `*Driver:* ${record.driver || 'N/A'}\n`;
            msg += `*Reason:* ${approval.reason}\n`;
            msg += `*Time:* ${timestamp}\n`;
            msg += `\n_Reply: !approve ${approval.id} or !reject ${approval.id}_\n`;
            msg += `───────────────────────\n`;
        }
        
        return msg;
    } catch (e) {
        return '[ERROR] Error reading approvals: ' + e.message;
    }
}

/**
 * Process an approval (approve or reject)
 */
async function processApproval(approvalId, approve) {
    const pendingFile = path.join(ROOT_DIR, 'data', 'pending_approvals.json');
    
    try {
        if (!fs.existsSync(pendingFile)) {
            return '[ERROR] No pending approvals found';
        }
        
        let approvals = JSON.parse(fs.readFileSync(pendingFile, 'utf8'));
        const approval = approvals.find(a => a.id === approvalId);
        
        if (!approval) {
            return `[ERROR] Approval ID *${approvalId}* not found`;
        }
        
        if (approval.status !== 'pending') {
            return `[WARN] Approval *${approvalId}* already ${approval.status}`;
        }
        
        if (approve) {
            // Mark as approved
            approval.status = 'approved';
            approval.approved_at = new Date().toISOString();
            
            // Handle all approval types that have records (car_cooldown, driver_change, edit)
            if (approval.record && ['car_cooldown', 'driver_change', 'edit'].includes(approval.type)) {
                // Save the record for processing
                const record = approval.record;
                const rawMsgFile = path.join(ROOT_DIR, 'data', 'raw_messages', `msg_approved_${approvalId}_${Date.now()}.json`);
                
                // Create a message file that the processor will pick up
                const msgData = {
                    id: `approved_${approvalId}`,
                    timestamp: Math.floor(Date.now() / 1000),
                    datetime: new Date().toISOString(),
                    groupName: 'Approved',
                    groupId: '',
                    senderPhone: '',
                    senderName: record.sender || 'Admin Approved',
                    body: `FUEL UPDATE\nDEPARTMENT: ${record.department || ''}\nDRIVER: ${record.driver || ''}\nCAR: ${record.car || ''}\nLITERS: ${record.liters || ''}\nAMOUNT: ${record.amount || ''}\nTYPE: ${record.type || ''}\nODOMETER: ${record.odometer || ''}`,
                    capturedAt: new Date().toISOString(),
                    isApproved: true,
                    approvalType: approval.type,
                    originalApprovalId: approvalId
                };
                
                fs.writeFileSync(rawMsgFile, JSON.stringify(msgData, null, 2));
            }
            
            fs.writeFileSync(pendingFile, JSON.stringify(approvals, null, 2));
            
            return `[APPROVED] Approved: *${approvalId}*\n\nThe record will be processed shortly.`;
        } else {
            // Mark as rejected
            approval.status = 'rejected';
            approval.rejected_at = new Date().toISOString();
            
            fs.writeFileSync(pendingFile, JSON.stringify(approvals, null, 2));
            
            return `[REJECTED] Rejected: *${approvalId}*\n\nThe record has been discarded.`;
        }
    } catch (e) {
        return '[ERROR] Error processing approval: ' + e.message;
    }
}

/**
 * Add vehicle to fleet for !add command
 */
function addVehicleToFleet(plate) {
    const fleetFile = path.join(ROOT_DIR, 'python', 'processor.py');
    const normalizedPlate = plate.replace(/\s+/g, '').toUpperCase();
    
    try {
        let content = fs.readFileSync(fleetFile, 'utf8');
        
        // Check if already exists
        if (content.includes(`'${normalizedPlate}'`)) {
            return `[WARN] Vehicle *${normalizedPlate}* is already in the fleet list`;
        }
        
        // Find the ALLOWED_PLATES set and add the new plate
        const match = content.match(/ALLOWED_PLATES\s*=\s*\{([^}]+)\}/s);
        if (match) {
            const existingPlates = match[1].trim();
            const newPlates = existingPlates + `, '${normalizedPlate}'`;
            content = content.replace(match[0], `ALLOWED_PLATES = {${newPlates}}`);
            
            fs.writeFileSync(fleetFile, content);
            return `[ADDED] Vehicle *${normalizedPlate}* added to fleet list`;
        }
        
        return '[ERROR] Could not find ALLOWED_PLATES in processor.py';
    } catch (e) {
        return '[ERROR] Error adding vehicle: ' + e.message;
    }
}

/**
 * Remove vehicle from fleet for !remove command
 */
function removeVehicleFromFleet(plate) {
    const fleetFile = path.join(ROOT_DIR, 'python', 'processor.py');
    const normalizedPlate = plate.replace(/\s+/g, '').toUpperCase();
    
    try {
        let content = fs.readFileSync(fleetFile, 'utf8');
        
        // Check if plate exists
        if (!content.includes(`'${normalizedPlate}'`)) {
            return `[WARN] Vehicle *${normalizedPlate}* is not in the fleet list`;
        }
        
        // Find and remove the plate from ALLOWED_PLATES set
        const match = content.match(/ALLOWED_PLATES\s*=\s*\{([^}]+)\}/s);
        if (match) {
            let platesStr = match[1];
            
            // Remove the plate (handle different positions: start, middle, end)
            platesStr = platesStr.replace(new RegExp(`'${normalizedPlate}',?\\s*`, 'g'), '');
            platesStr = platesStr.replace(new RegExp(`,\\s*'${normalizedPlate}'`, 'g'), '');
            
            // Clean up any trailing commas or extra whitespace
            platesStr = platesStr.replace(/,\s*$/, '').replace(/^\s*,/, '').trim();
            
            content = content.replace(match[0], `ALLOWED_PLATES = {${platesStr}}`);
            
            fs.writeFileSync(fleetFile, content);
            return `[REMOVED] Vehicle *${normalizedPlate}* removed from fleet list`;
        }
        
        return '[ERROR] Could not find ALLOWED_PLATES in processor.py';
    } catch (e) {
        return '[ERROR] Error removing vehicle: ' + e.message;
    }
}

/**
 * Extract phone number from LID using participant cache
 * WhatsApp links LIDs to phone numbers for users in groups
 */
async function extractPhoneFromLid(msg, chat) {
    const senderId = msg.author || msg.from;
    if (!senderId?.includes('@lid')) return null;
    
    const lidUser = senderId.split('@')[0];
    
    // Method 1: Check msg._data for participant phone
    try {
        if (msg._data) {
            // Check for participant field (sometimes has phone@c.us)
            const dataFields = ['participant', 'from', 'author', 'sender'];
            for (const field of dataFields) {
                const value = msg._data[field];
                if (value && typeof value === 'string' && value.includes('@c.us')) {
                    return value.split('@')[0];
                }
                // Handle object format { user: '254...', server: 'c.us' }
                if (value && typeof value === 'object') {
                    if (value.server === 'c.us' && value.user) {
                        return value.user;
                    }
                }
            }
        }
    } catch (e) {}
    
    // Method 2: Try to get phone from message id participant
    try {
        if (msg.id && msg.id.participant) {
            const participant = msg.id.participant;
            if (typeof participant === 'string' && participant.includes('@c.us')) {
                return participant.split('@')[0];
            }
            if (typeof participant === 'object' && participant.user) {
                return participant.user;
            }
        }
    } catch (e) {}
    
    // Method 3: Iterate through chat participants and check their LID
    try {
        const participants = chat.participants || [];
        for (const p of participants) {
            try {
                // Check if participant has lid matching sender
                if (p.id && p.id.lid) {
                    const pLidUser = p.id.lid.split('@')[0];
                    if (pLidUser === lidUser) {
                        return p.id.user;
                    }
                }
            } catch (e) {}
        }
    } catch (e) {}
    
    // Method 4: Try contact resolution (may fail for some contacts)
    try {
        const contact = await msg.getContact();
        if (contact) {
            // Try different properties that might have phone
            if (contact.number) return contact.number.replace(/^\+/, '');
            if (contact.id && contact.id.user) return contact.id.user;
        }
    } catch (e) {}
    
    return null;
}

/**
 * Get all group admin phone numbers for mentions
 */
async function getGroupAdminPhones() {
    if (!client || !isReady || !targetGroupId) {
        return [];
    }
    
    try {
        const chat = await client.getChatById(targetGroupId);
        if (!chat || !chat.isGroup) return [];
        
        const participants = chat.participants || [];
        const adminPhones = [];
        
        for (const participant of participants) {
            if (participant.isAdmin || participant.isSuperAdmin) {
                const phone = participant.id._serialized?.split('@')[0];
                if (phone && !phone.includes('lid')) {
                    adminPhones.push(phone);
                }
            }
        }
        
        return adminPhones;
    } catch (e) {
        console.error('Error getting admin phones:', e.message);
        return [];
    }
}

/**
 * Send a message with mentions to the target group
 * @param {string} message - The message text
 * @param {string[]} mentionPhones - Array of phone numbers to mention (without @c.us)
 */
async function sendGroupMessageWithMentions(message, mentionPhones = []) {
    if (!client || !isReady || !targetGroupId) {
        console.log('[WARN] Cannot send message: client not ready or no target group');
        return false;
    }
    
    // Rate limiting to prevent spam/bans
    const now = Date.now();
    const timeSinceLastMessage = now - lastMessageTime;
    if (timeSinceLastMessage < MESSAGE_COOLDOWN_MS) {
        await new Promise(resolve => setTimeout(resolve, MESSAGE_COOLDOWN_MS - timeSinceLastMessage));
    }
    
    try {
        if (mentionPhones.length > 0) {
            // Create Contact objects for mentions
            const mentions = [];
            for (const phone of mentionPhones) {
                try {
                    const contactId = `${phone}@c.us`;
                    const contact = await client.getContactById(contactId);
                    if (contact) {
                        mentions.push(contact);
                    }
                } catch (e) {
                    // Contact not found, skip
                }
            }
            
            if (mentions.length > 0) {
                await client.sendMessage(targetGroupId, message, { mentions });
            } else {
                await client.sendMessage(targetGroupId, message);
            }
        } else {
            await client.sendMessage(targetGroupId, message);
        }
        
        lastMessageTime = Date.now();
        console.log('[SENT] Sent message to group' + (mentionPhones.length > 0 ? ` (with ${mentionPhones.length} mentions)` : ''));
        return true;
    } catch (error) {
        console.error('[ERROR] Error sending message:', error.message);
        return false;
    }
}

/**
 * Check if sender is a group admin
 */
async function isGroupAdmin(msg) {
    try {
        const chat = await msg.getChat();
        if (!chat.isGroup) return false;
        
        // Get the sender's ID - try multiple sources
        let senderId;
        let senderPhone = null;
        
        if (msg.fromMe) {
            // Message from ourselves - use our own WhatsApp ID
            senderId = client.info.wid._serialized;
            senderPhone = client.info.wid.user;
        } else {
            // Message from someone else in the group
            senderId = msg.author || msg.from;
            
            // Try to extract phone from raw data (works for LID format)
            try {
                if (msg._data && msg._data.author) {
                    // Sometimes the raw author contains phone@c.us
                    const rawAuthor = msg._data.author;
                    if (rawAuthor && typeof rawAuthor === 'string' && rawAuthor.includes('@c.us')) {
                        senderPhone = rawAuthor.split('@')[0];
                    }
                    // Handle object format { user: '254...', server: 'c.us' }
                    if (rawAuthor && typeof rawAuthor === 'object' && rawAuthor.user) {
                        senderPhone = rawAuthor.user;
                    }
                }
                // Also check participant field in msg._data
                if (!senderPhone && msg._data && msg._data.participant) {
                    const rawParticipant = msg._data.participant;
                    if (rawParticipant && typeof rawParticipant === 'string' && rawParticipant.includes('@c.us')) {
                        senderPhone = rawParticipant.split('@')[0];
                    }
                    if (rawParticipant && typeof rawParticipant === 'object' && rawParticipant.user) {
                        senderPhone = rawParticipant.user;
                    }
                }
                // Also check msg.id.participant
                if (!senderPhone && msg.id && msg.id.participant) {
                    const participant = msg.id.participant;
                    if (typeof participant === 'string' && participant.includes('@c.us')) {
                        senderPhone = participant.split('@')[0];
                    }
                    if (typeof participant === 'object' && participant.user) {
                        senderPhone = participant.user;
                    }
                }
            } catch (e) {
                // Ignore extraction errors
            }
            
            // If still no phone and using LID format, try advanced extraction
            if (!senderPhone && senderId?.includes('@lid')) {
                senderPhone = await extractPhoneFromLid(msg, chat);
            }
        }
        
        console.log(`[CHECK] Checking admin status for: ${senderId} (fromMe: ${msg.fromMe}, phone: ${senderPhone || 'unknown'})`);
        
        const participants = chat.participants || [];
        
        // First, try to find by exact ID match
        for (const participant of participants) {
            const participantId = participant.id._serialized;
            
            if (participantId === senderId) {
                const isAdminUser = participant.isAdmin || participant.isSuperAdmin;
                console.log(`[OK] Found participant (exact match): ${participantId}, isAdmin: ${isAdminUser}`);
                return isAdminUser;
            }
        }
        
        // If we have a phone number, try matching by phone
        if (senderPhone) {
            for (const participant of participants) {
                const participantId = participant.id._serialized;
                const participantPhone = participantId?.split('@')[0];
                
                // Match with or without country code prefix variations
                if (participantPhone === senderPhone || 
                    participantPhone === senderPhone.replace(/^\+/, '') ||
                    senderPhone === participantPhone.replace(/^\+/, '')) {
                    const isAdminUser = participant.isAdmin || participant.isSuperAdmin;
                    console.log(`[OK] Found participant (phone match): ${participantId}, isAdmin: ${isAdminUser}`);
                    return isAdminUser;
                }
            }
        }
        
        // If sender uses @lid format and we still haven't matched, try more methods
        if (senderId?.includes('@lid')) {
            console.log(`[INFO] Sender uses LID format, attempting additional resolution...`);
            
            // Check if any participant has matching LID in their id object
            const senderLidUser = senderId.split('@')[0];
            for (const participant of participants) {
                try {
                    // Some WhatsApp versions include lid in the id object
                    if (participant.id && participant.id.lid) {
                        const pLidUser = participant.id.lid.split('@')[0];
                        if (pLidUser === senderLidUser) {
                            const isAdminUser = participant.isAdmin || participant.isSuperAdmin;
                            console.log(`[OK] Found participant (LID in id): ${participant.id._serialized}, isAdmin: ${isAdminUser}`);
                            return isAdminUser;
                        }
                    }
                } catch (e) {}
            }
            
            // Try contact resolution as last resort
            try {
                const contact = await msg.getContact();
                if (contact && (contact.number || (contact.id && contact.id.user))) {
                    const phoneNumber = contact.number || contact.id.user;
                    console.log(`[PHONE] Resolved LID to phone: ${phoneNumber}`);
                    
                    // Search participants by phone number
                    for (const participant of participants) {
                        const participantId = participant.id._serialized;
                        const participantPhone = participantId?.split('@')[0];
                        
                        if (participantPhone === phoneNumber || 
                            participantPhone === phoneNumber.replace(/^\+/, '') ||
                            phoneNumber === participantPhone) {
                            const isAdminUser = participant.isAdmin || participant.isSuperAdmin;
                            console.log(`[OK] Found participant (via contact phone): ${participantId}, isAdmin: ${isAdminUser}`);
                            return isAdminUser;
                        }
                    }
                }
            } catch (contactErr) {
                console.log(`[WARN] Could not resolve contact: ${contactErr.message}`);
            }
        }
        
        // Fallback: Check phone number match (for non-lid formats)
        const senderIdPhone = senderId?.split('@')[0];
        for (const participant of participants) {
            const participantId = participant.id._serialized;
            const participantPhone = participantId?.split('@')[0];
            
            if (senderIdPhone === participantPhone) {
                const isAdminUser = participant.isAdmin || participant.isSuperAdmin;
                console.log(`[OK] Found participant (phone match): ${participantId}, isAdmin: ${isAdminUser}`);
                return isAdminUser;
            }
        }
        
        // Not found - log for debugging with more detail
        console.log(`[WARN] Sender ${senderId} not found in participants.`);
        if (senderPhone) {
            console.log(`   [PHONE] Extracted phone: ${senderPhone}`);
        }
        console.log(`   Sample participants:`);
        participants.slice(0, 5).forEach(p => {
            const lidInfo = p.id.lid ? ` (lid: ${p.id.lid})` : '';
            console.log(`   - ${p.id._serialized}${lidInfo} (admin: ${p.isAdmin || p.isSuperAdmin})`);
        });
        // Debug: Log available msg._data fields for troubleshooting
        if (msg._data) {
            const relevantFields = ['author', 'participant', 'from', 'sender', 'notifyName'];
            const found = {};
            for (const field of relevantFields) {
                if (msg._data[field] !== undefined) {
                    found[field] = typeof msg._data[field] === 'object' 
                        ? JSON.stringify(msg._data[field]) 
                        : msg._data[field];
                }
            }
            if (Object.keys(found).length > 0) {
                console.log(`   [DEBUG] Available msg._data fields:`, found);
            }
        }
        
        return false;
    } catch (e) {
        console.error('Error checking admin status:', e.message);
        return false;
    }
}

/**
 * Get the sender's name from a WhatsApp message
 */
function getSenderName(msg) {
    // Try to get the display name
    if (msg._data && msg._data.notifyName) {
        return msg._data.notifyName;
    }
    // Fallback to phone number
    const contact = msg.author || msg.from;
    if (contact) {
        return contact.split('@')[0];
    }
    return 'Unknown';
}

/**
 * Get public commands help
 */
function getPublicCommandsHelp() {
    let help = `[HELP] *AVAILABLE COMMANDS*\n`;
    help += `------------------------------------\n\n`;
    
    help += `*Everyone can use:*\n\n`;
    help += `!how - How to send a fuel update\n`;
    help += `!myrecords - View your recent fuel records\n`;
    help += `!myefficiency - View your fuel efficiency stats\n`;
    help += `!myvehicles - View vehicles you've fueled\n`;
    help += `!commands - Show this help\n\n`;
    
    help += `*Natural language:*\n`;
    help += `"fuel today" - Today's fuel summary\n`;
    help += `"how much KCA542Q" - Vehicle fuel usage\n\n`;
    
    help += `_Admin commands: type !help_`;
    
    return help;
}

/**
 * Get driver's recent fuel records
 */
async function getDriverRecords(msg) {
    const senderName = getSenderName(msg);
    const driverHistoryFile = path.join(ROOT_DIR, 'data', 'driver_history.json');
    
    // Run Python script to get driver records
    try {
        const queryScript = path.join(ROOT_DIR, 'python', 'processor.py');
        const { execSync } = require('child_process');
        const cmd = buildPythonCommand(queryScript, '--driver-query', `"${senderName}"`, '--limit', '10');
        
        try {
            execSync(cmd, { cwd: ROOT_DIR, shell: true, timeout: 30000 });
        } catch (e) {
            // Script might not support this yet, use fallback
        }
        
        // Read from driver history if available
        if (fs.existsSync(driverHistoryFile)) {
            const history = JSON.parse(fs.readFileSync(driverHistoryFile, 'utf8'));
            const driverRecords = history[senderName.toUpperCase()] || history[senderName] || [];
            
            if (driverRecords.length === 0) {
                return `[INFO] *No Records Found*\n\nNo fuel records found for "${senderName}".\n\n_Make sure your DRIVER name matches exactly._`;
            }
            
            let response = `[RECORDS] *Your Recent Fuel Records*\n`;
            response += `------------------------------------\n`;
            response += `Driver: ${senderName}\n\n`;
            
            const recentRecords = driverRecords.slice(-5).reverse();
            recentRecords.forEach((r, i) => {
                response += `${i + 1}. ${r.car} - ${r.liters}L (KSH ${parseFloat(r.amount || 0).toLocaleString()})\n`;
                response += `   ${r.datetime || 'N/A'}\n\n`;
            });
            
            response += `_Total records: ${driverRecords.length}_`;
            return response;
        }
        
        // Fallback: search Excel via Python
        return await searchDriverRecordsFromExcel(senderName);
        
    } catch (e) {
        console.error('Error getting driver records:', e.message);
        return `[ERROR] Could not retrieve records. Please try again.`;
    }
}

/**
 * Search driver records from Excel file
 */
async function searchDriverRecordsFromExcel(driverName) {
    try {
        const queryScript = path.join(ROOT_DIR, 'python', 'processor.py');
        const { execSync } = require('child_process');
        const outputFile = path.join(ROOT_DIR, 'data', 'query_result.json');
        
        const cmd = buildPythonCommand(queryScript, '--query-driver', `"${driverName}"`, '--output', `"${outputFile}"`);
        
        try {
            execSync(cmd, { cwd: ROOT_DIR, shell: true, timeout: 30000 });
            
            if (fs.existsSync(outputFile)) {
                const result = JSON.parse(fs.readFileSync(outputFile, 'utf8'));
                if (result.records && result.records.length > 0) {
                    let response = `[RECORDS] *Your Recent Fuel Records*\n`;
                    response += `------------------------------------\n`;
                    response += `Driver: ${driverName}\n\n`;
                    
                    result.records.slice(0, 5).forEach((r, i) => {
                        response += `${i + 1}. ${r.car} - ${r.liters}L (KSH ${parseFloat(r.amount || 0).toLocaleString()})\n`;
                        response += `   ${r.datetime || 'N/A'}\n\n`;
                    });
                    
                    response += `_Total records: ${result.total || result.records.length}_`;
                    return response;
                }
            }
        } catch (e) {
            // Python query failed
        }
        
        return `[INFO] *No Records Found*\n\nNo fuel records found for "${driverName}".\n\n_Make sure your DRIVER name in fuel reports matches your WhatsApp name._`;
        
    } catch (e) {
        console.error('Error searching Excel:', e.message);
        return `[ERROR] Could not search records. Please try again.`;
    }
}

/**
 * Get driver's fuel efficiency stats
 */
async function getDriverEfficiency(msg) {
    const senderName = getSenderName(msg);
    const efficiencyFile = path.join(ROOT_DIR, 'data', 'efficiency_history.json');
    
    try {
        if (!fs.existsSync(efficiencyFile)) {
            return `[INFO] *No Efficiency Data*\n\nNo efficiency records available yet.\n\n_Efficiency is calculated after multiple fuel-ups for the same vehicle._`;
        }
        
        const history = JSON.parse(fs.readFileSync(efficiencyFile, 'utf8'));
        
        // Filter records for this driver
        const driverRecords = history.filter(r => 
            r.driver && r.driver.toUpperCase() === senderName.toUpperCase()
        );
        
        if (driverRecords.length === 0) {
            return `[INFO] *No Efficiency Data*\n\nNo efficiency records found for "${senderName}".\n\n_Efficiency is calculated after your second fuel-up for a vehicle._`;
        }
        
        // Calculate stats
        const efficiencies = driverRecords.map(r => r.efficiency).filter(e => e > 0);
        const avgEfficiency = efficiencies.reduce((a, b) => a + b, 0) / efficiencies.length;
        const minEfficiency = Math.min(...efficiencies);
        const maxEfficiency = Math.max(...efficiencies);
        const totalDistance = driverRecords.reduce((a, r) => a + (r.distance || 0), 0);
        const totalLiters = driverRecords.reduce((a, r) => a + (r.liters || 0), 0);
        
        // Get efficiency rating
        let rating = 'Normal';
        let ratingIcon = '[OK]';
        if (avgEfficiency >= 6 && avgEfficiency <= 12) {
            rating = 'Good';
            ratingIcon = '[GOOD]';
        } else if (avgEfficiency < 4) {
            rating = 'Poor - Check vehicle';
            ratingIcon = '[!]';
        } else if (avgEfficiency > 20) {
            rating = 'Unusually high';
            ratingIcon = '[?]';
        }
        
        let response = `[STATS] *Your Fuel Efficiency*\n`;
        response += `------------------------------------\n`;
        response += `Driver: ${senderName}\n\n`;
        
        response += `${ratingIcon} Average: *${avgEfficiency.toFixed(1)} km/L*\n`;
        response += `Rating: ${rating}\n\n`;
        
        response += `Range: ${minEfficiency.toFixed(1)} - ${maxEfficiency.toFixed(1)} km/L\n`;
        response += `Total Distance: ${totalDistance.toLocaleString()} km\n`;
        response += `Total Fuel: ${totalLiters.toFixed(1)} L\n\n`;
        
        response += `_Based on ${driverRecords.length} records_`;
        
        return response;
        
    } catch (e) {
        console.error('Error getting efficiency:', e.message);
        return `[ERROR] Could not retrieve efficiency data. Please try again.`;
    }
}

/**
 * Get vehicles the driver has fueled
 */
async function getDriverVehicles(msg) {
    const senderName = getSenderName(msg);
    const carLastUpdateFile = path.join(ROOT_DIR, 'data', 'car_last_update.json');
    const efficiencyFile = path.join(ROOT_DIR, 'data', 'efficiency_history.json');
    
    try {
        let vehicles = new Map(); // plate -> { count, lastDate, totalLiters }
        
        // Check efficiency history for this driver
        if (fs.existsSync(efficiencyFile)) {
            const history = JSON.parse(fs.readFileSync(efficiencyFile, 'utf8'));
            const driverRecords = history.filter(r => 
                r.driver && r.driver.toUpperCase() === senderName.toUpperCase()
            );
            
            driverRecords.forEach(r => {
                const existing = vehicles.get(r.car) || { count: 0, lastDate: '', totalLiters: 0 };
                existing.count++;
                existing.totalLiters += r.liters || 0;
                if (r.timestamp > existing.lastDate) {
                    existing.lastDate = r.timestamp;
                }
                vehicles.set(r.car, existing);
            });
        }
        
        // Also check car_last_update for recent activity
        if (fs.existsSync(carLastUpdateFile)) {
            const updates = JSON.parse(fs.readFileSync(carLastUpdateFile, 'utf8'));
            for (const [plate, data] of Object.entries(updates)) {
                if (data.driver && data.driver.toUpperCase() === senderName.toUpperCase()) {
                    const existing = vehicles.get(plate) || { count: 0, lastDate: '', totalLiters: 0 };
                    existing.lastDate = data.timestamp || existing.lastDate;
                    vehicles.set(plate, existing);
                }
            }
        }
        
        if (vehicles.size === 0) {
            return `[INFO] *No Vehicles Found*\n\nNo vehicles found for driver "${senderName}".\n\n_Make sure your DRIVER name matches your WhatsApp name._`;
        }
        
        let response = `[VEHICLES] *Your Vehicles*\n`;
        response += `------------------------------------\n`;
        response += `Driver: ${senderName}\n\n`;
        
        let i = 1;
        for (const [plate, data] of vehicles) {
            response += `${i}. *${plate}*\n`;
            if (data.count > 0) response += `   Fuel-ups: ${data.count}\n`;
            if (data.totalLiters > 0) response += `   Total: ${data.totalLiters.toFixed(1)} L\n`;
            if (data.lastDate) {
                const lastDate = new Date(data.lastDate);
                response += `   Last: ${lastDate.toLocaleDateString()}\n`;
            }
            response += `\n`;
            i++;
        }
        
        response += `_Total vehicles: ${vehicles.size}_`;
        
        return response;
        
    } catch (e) {
        console.error('Error getting driver vehicles:', e.message);
        return `[ERROR] Could not retrieve vehicle data. Please try again.`;
    }
}

/**
 * Handle natural language queries
 */
async function handleNaturalQuery(msg, text) {
    const lowerText = text.toLowerCase();
    
    // "fuel today" - Today's summary
    if (lowerText.includes('fuel today') || lowerText.includes('today fuel') || lowerText === 'today') {
        return await getTodayFuelSummary();
    }
    
    // "how much KCA542Q" or "fuel KCA542Q" - Vehicle query
    const plateMatch = text.match(/([A-Z]{2,4}\s*\d{2,4}\s*[A-Z]?)/i);
    if (plateMatch) {
        const plate = plateMatch[1].replace(/\s+/g, '').toUpperCase();
        return await getVehicleFuelSummary(plate);
    }
    
    // "fuel this week" or "weekly fuel"
    if (lowerText.includes('this week') || lowerText.includes('weekly')) {
        return await getWeeklyFuelSummary();
    }
    
    return null; // Not recognized
}

/**
 * Get today's fuel summary
 */
async function getTodayFuelSummary() {
    try {
        const carLastUpdateFile = path.join(ROOT_DIR, 'data', 'car_last_update.json');
        
        if (!fs.existsSync(carLastUpdateFile)) {
            return `[INFO] *No Fuel Data Today*\n\nNo fuel records available.`;
        }
        
        const updates = JSON.parse(fs.readFileSync(carLastUpdateFile, 'utf8'));
        const today = new Date().toISOString().split('T')[0];
        
        let todayRecords = [];
        let totalLiters = 0;
        let totalAmount = 0;
        
        for (const [plate, data] of Object.entries(updates)) {
            if (data.timestamp && data.timestamp.startsWith(today)) {
                todayRecords.push({ plate, ...data });
                totalLiters += parseFloat(data.liters) || 0;
                totalAmount += parseFloat(data.amount) || 0;
            }
        }
        
        if (todayRecords.length === 0) {
            return `[INFO] *No Fuel Records Today*\n\nNo vehicles have fueled today yet.`;
        }
        
        let response = `[TODAY] *Today's Fuel Summary*\n`;
        response += `------------------------------------\n`;
        response += `Date: ${new Date().toLocaleDateString()}\n\n`;
        
        response += `Vehicles Fueled: *${todayRecords.length}*\n`;
        response += `Total Fuel: *${totalLiters.toFixed(1)} L*\n`;
        response += `Total Spent: *KSH ${totalAmount.toLocaleString()}*\n\n`;
        
        response += `Recent:\n`;
        todayRecords.slice(0, 5).forEach(r => {
            response += `- ${r.plate}: ${r.liters}L by ${r.driver || 'N/A'}\n`;
        });
        
        return response;
        
    } catch (e) {
        console.error('Error getting today summary:', e.message);
        return `[ERROR] Could not retrieve today's summary.`;
    }
}

/**
 * Get weekly fuel summary
 */
async function getWeeklyFuelSummary() {
    try {
        const summaryScript = path.join(ROOT_DIR, 'python', 'weekly_summary.py');
        const { execSync } = require('child_process');
        const cmd = buildPythonCommand(summaryScript, '7');
        
        try {
            execSync(cmd, { cwd: ROOT_DIR, shell: true, timeout: 30000 });
        } catch (e) {
            // Continue and try to read existing summary
        }
        
        const summaryFile = path.join(ROOT_DIR, 'data', 'weekly_summary.json');
        if (fs.existsSync(summaryFile)) {
            const summary = JSON.parse(fs.readFileSync(summaryFile, 'utf8'));
            return summary.message || `[REPORT] Weekly summary generated.`;
        }
        
        return `[INFO] Weekly summary not available.`;
        
    } catch (e) {
        console.error('Error getting weekly summary:', e.message);
        return `[ERROR] Could not generate weekly summary.`;
    }
}

/**
 * Get vehicle fuel summary
 */
async function getVehicleFuelSummary(plate) {
    try {
        const carLastUpdateFile = path.join(ROOT_DIR, 'data', 'car_last_update.json');
        const efficiencyFile = path.join(ROOT_DIR, 'data', 'efficiency_history.json');
        
        let response = `[VEHICLE] *${plate}*\n`;
        response += `------------------------------------\n\n`;
        
        // Get last update
        if (fs.existsSync(carLastUpdateFile)) {
            const updates = JSON.parse(fs.readFileSync(carLastUpdateFile, 'utf8'));
            const data = updates[plate];
            
            if (data) {
                response += `*Last Fuel-up:*\n`;
                response += `Date: ${new Date(data.timestamp).toLocaleString()}\n`;
                response += `Driver: ${data.driver || 'N/A'}\n`;
                response += `Fuel: ${data.liters} L (${data.type || 'N/A'})\n`;
                response += `Amount: KSH ${parseFloat(data.amount || 0).toLocaleString()}\n`;
                response += `Odometer: ${parseInt(data.odometer || 0).toLocaleString()} km\n`;
                if (data.efficiency) {
                    response += `Efficiency: ${data.efficiency.toFixed(1)} km/L\n`;
                }
                response += `\n`;
            } else {
                response += `No recent records found for this vehicle.\n\n`;
            }
        }
        
        // Get efficiency history
        if (fs.existsSync(efficiencyFile)) {
            const history = JSON.parse(fs.readFileSync(efficiencyFile, 'utf8'));
            const vehicleRecords = history.filter(r => r.car === plate);
            
            if (vehicleRecords.length > 0) {
                const efficiencies = vehicleRecords.map(r => r.efficiency).filter(e => e > 0);
                const avgEfficiency = efficiencies.reduce((a, b) => a + b, 0) / efficiencies.length;
                const totalDistance = vehicleRecords.reduce((a, r) => a + (r.distance || 0), 0);
                
                response += `*Efficiency Stats:*\n`;
                response += `Average: ${avgEfficiency.toFixed(1)} km/L\n`;
                response += `Total Distance: ${totalDistance.toLocaleString()} km\n`;
                response += `Records: ${vehicleRecords.length}\n`;
            }
        }
        
        return response;
        
    } catch (e) {
        console.error('Error getting vehicle summary:', e.message);
        return `[ERROR] Could not retrieve data for ${plate}.`;
    }
}

/**
 * Get the fuel update guide message for !how command (available to everyone)
 */
function getFuelUpdateGuide() {
    let guide = `[GUIDE] *HOW TO SEND A FUEL UPDATE*\n`;
    guide += `------------------------------------\n\n`;
    
    guide += `Your message *MUST* start with:\n`;
    guide += `*FUEL UPDATE*\n\n`;
    
    guide += `Then include *ALL* these fields:\n\n`;
    
    guide += `[1] *DEPARTMENT:* Your department\n`;
    guide += `   _(e.g., LOGISTICS, SALES, OPERATIONS)_\n\n`;
    
    guide += `[2] *DRIVER:* Your name\n`;
    guide += `   _(e.g., John Kamau)_\n\n`;
    
    guide += `[3] *CAR:* Vehicle registration plate\n`;
    guide += `   _(e.g., KCA 542Q)_\n\n`;
    
    guide += `[4] *LITERS:* Fuel amount in liters\n`;
    guide += `   _(e.g., 45.5)_\n\n`;
    
    guide += `[5] *AMOUNT:* Cost in KSH\n`;
    guide += `   _(e.g., 7,500)_\n\n`;
    
    guide += `[6] *TYPE:* Fuel type\n`;
    guide += `   _(DIESEL, PETROL, SUPER, V-POWER, or UNLEADED)_\n\n`;
    
    guide += `[7] *ODOMETER:* Current odometer reading\n`;
    guide += `   _(e.g., 125,430)_\n\n`;
    
    guide += `------------------------------------\n`;
    guide += `[OK] *EXAMPLE MESSAGE:*\n`;
    guide += `------------------------------------\n\n`;
    
    guide += `FUEL UPDATE\n`;
    guide += `DEPARTMENT: LOGISTICS\n`;
    guide += `DRIVER: John Kamau\n`;
    guide += `CAR: KCA 542Q\n`;
    guide += `LITERS: 45.5\n`;
    guide += `AMOUNT: 7,500\n`;
    guide += `TYPE: DIESEL\n`;
    guide += `ODOMETER: 125,430\n\n`;
    
    guide += `------------------------------------\n`;
    guide += `[!] *IMPORTANT NOTES:*\n`;
    guide += `------------------------------------\n\n`;
    
    guide += `- Message MUST start with *FUEL UPDATE*\n`;
    guide += `- ALL 7 fields are *required*\n`;
    guide += `- Vehicle must be in approved fleet list\n`;
    guide += `- Odometer must be higher than last reading\n`;
    guide += `- Fields can be in any order\n`;
    guide += `- You can use : or - or = as separator\n\n`;
    
    guide += `_Type !how anytime to see this guide again._`;
    
    return guide;
}

/**
 * Handle public commands (available to everyone)
 */
async function handlePublicCommand(msg, body) {
    const text = body.trim().toLowerCase();
    const parts = text.split(/\s+/);
    const command = parts[0];
    
    if (command === '!how') {
        const guide = getFuelUpdateGuide();
        await sendGroupMessage(guide);
        console.log(`[CMD] Public command: !how`);
        return true;
    }
    
    // Driver query commands
    if (command === '!myrecords') {
        const response = await getDriverRecords(msg);
        await sendGroupMessage(response);
        console.log(`[CMD] Public command: !myrecords`);
        return true;
    }
    
    if (command === '!myefficiency') {
        const response = await getDriverEfficiency(msg);
        await sendGroupMessage(response);
        console.log(`[CMD] Public command: !myefficiency`);
        return true;
    }
    
    if (command === '!myvehicles') {
        const response = await getDriverVehicles(msg);
        await sendGroupMessage(response);
        console.log(`[CMD] Public command: !myvehicles`);
        return true;
    }
    
    if (command === '!commands') {
        const response = getPublicCommandsHelp();
        await sendGroupMessage(response);
        console.log(`[CMD] Public command: !commands`);
        return true;
    }
    
    // Natural language queries
    if (text.startsWith('fuel ') || text.startsWith('how much ') || text.startsWith('what is ')) {
        const response = await handleNaturalQuery(msg, text);
        if (response) {
            await sendGroupMessage(response);
            console.log(`[CMD] Natural query handled`);
            return true;
        }
    }
    
    return false; // Not a public command
}

/**
 * Handle admin commands (restricted to group admins only)
 */
async function handleAdminCommand(msg, body) {
    const text = body.trim().toLowerCase();
    const parts = text.split(/\s+/);
    const command = parts[0];
    
    // First check if it's a public command (available to everyone)
    if (await handlePublicCommand(msg, body)) {
        return;
    }
    
    // Check if sender is a group admin
    const isAdmin = await isGroupAdmin(msg);
    if (!isAdmin) {
        await sendGroupMessage('[DENIED] *Access Denied*\n\nOnly group admins can use admin commands.');
        console.log(`[BLOCKED] Non-admin tried to use: ${command}`);
        return;
    }
    
    let response = '';
    
    switch (command) {
        case '!status':
            response = getSystemStatus();
            break;
            
        case '!summary':
            // Determine period: daily (1 day), weekly (7 days), monthly (30 days)
            let days = 7;  // Default to weekly
            
            if (parts.length >= 2) {
                const period = parts[1];
                if (period === 'daily' || period === 'd') {
                    days = 1;
                } else if (period === 'weekly' || period === 'w') {
                    days = 7;
                } else if (period === 'monthly' || period === 'm') {
                    days = 30;
                }
            }
            
            const summaryScript = path.join(ROOT_DIR, 'python', 'weekly_summary.py');
            try {
                const { execSync } = require('child_process');
                const cmd = buildPythonCommand(summaryScript, days.toString());
                execSync(cmd, { cwd: ROOT_DIR, shell: true });
                
                // Read the generated summary
                const summaryFile = path.join(ROOT_DIR, 'data', 'weekly_summary.json');
                if (fs.existsSync(summaryFile)) {
                    const summary = JSON.parse(fs.readFileSync(summaryFile, 'utf8'));
                    response = summary.message || '[REPORT] Summary generated';
                } else {
                    response = '[ERROR] Summary file not found';
                }
            } catch (e) {
                response = '[ERROR] Error generating summary: ' + e.message;
            }
            break;
            
        case '!add':
            if (parts.length < 2) {
                response = '[USAGE] Usage: !add KXX 123Y';
            } else {
                const plate = parts.slice(1).join('');
                response = addVehicleToFleet(plate);
            }
            break;
            
        case '!remove':
            if (parts.length < 2) {
                response = '[USAGE] Usage: !remove KXX 123Y';
            } else {
                const plate = parts.slice(1).join('');
                response = removeVehicleFromFleet(plate);
            }
            break;
            
        case '!list':
            response = getFleetList();
            break;
        
        case '!car':
            if (parts.length < 2) {
                response = '[USAGE] Usage: !car KXX123Y [days]\n\nExample: !car KCA542Q 30\nExample: !car KCZ 181P 60';
            } else {
                // Handle plates with spaces: !car KCZ 181P 30 or !car KCZ181P 30
                // The last part might be the days number, or part of the plate
                let carPlate = '';
                let carDays = 30; // default
                
                // Check if the last part is a number (days)
                const lastPart = parts[parts.length - 1];
                if (/^\d+$/.test(lastPart) && parts.length > 2) {
                    // Last part is days, everything in between is the plate
                    carPlate = parts.slice(1, -1).join('').toUpperCase();
                    carDays = parseInt(lastPart);
                } else {
                    // No days specified, everything after !car is the plate
                    carPlate = parts.slice(1).join('').toUpperCase();
                }
                
                const carSummaryScript = path.join(ROOT_DIR, 'python', 'weekly_summary.py');
                try {
                    const { execSync } = require('child_process');
                    const cmd = buildPythonCommand(carSummaryScript, '--car', carPlate, carDays.toString());
                    console.log(`[CAR] Running car summary for ${carPlate} (${carDays} days)`);
                    execSync(cmd, { cwd: ROOT_DIR, shell: true });
                    
                    const carSummaryFile = path.join(ROOT_DIR, 'data', 'car_summary.json');
                    if (fs.existsSync(carSummaryFile)) {
                        const carSummary = JSON.parse(fs.readFileSync(carSummaryFile, 'utf8'));
                        response = carSummary.message || '[INFO] No data found for this vehicle';
                    } else {
                        response = '[ERROR] Could not generate car summary';
                    }
                } catch (e) {
                    console.error('Car summary error:', e.message);
                    response = '[ERROR] Error: ' + e.message;
                }
            }
            break;
        
        case '!pending':
            response = getPendingApprovals();
            break;
        
        case '!approve':
            if (parts.length < 2) {
                response = '[USAGE] Usage: !approve <ID>\n\nUse !pending to see pending approvals.';
            } else {
                const approvalId = parts[1];
                response = await processApproval(approvalId, true);
            }
            break;
        
        case '!reject':
            if (parts.length < 2) {
                response = '[USAGE] Usage: !reject <ID>\n\nUse !pending to see pending approvals.';
            } else {
                const rejectId = parts[1];
                response = await processApproval(rejectId, false);
            }
            break;
            
        case '!help':
            response = `[HELP] *ADMIN COMMANDS*\n`;
            response += `----------------------------\n\n`;
            response += `*!status* - System health check\n`;
            response += `*!summary* - Get weekly summary\n`;
            response += `*!summary daily* - Get today's summary\n`;
            response += `*!summary weekly* - Get weekly summary\n`;
            response += `*!summary monthly* - Get monthly summary\n`;
            response += `*!car KXX123Y* - Get vehicle summary\n`;
            response += `*!car KXX123Y 60* - Vehicle summary (60 days)\n`;
            response += `*!pending* - View pending approvals\n`;
            response += `*!approve ID* - Approve pending record\n`;
            response += `*!reject ID* - Reject pending record\n`;
            response += `*!add KXX 123Y* - Add vehicle to fleet\n`;
            response += `*!remove KXX 123Y* - Remove vehicle\n`;
            response += `*!list* - List all fleet vehicles\n`;
            response += `*!help* - Show this help\n\n`;
            response += `_Only group admins can use these commands._\n\n`;
            response += `----------------------------\n`;
            response += `[PUBLIC] *PUBLIC COMMANDS*\n`;
            response += `----------------------------\n\n`;
            response += `*!how* - Guide on sending fuel updates\n`;
            response += `_Available to everyone._`;
            break;
            
        default:
            // Unknown command - don't respond
            return;
    }
    
    if (response) {
        await sendGroupMessage(response);
        console.log(`[CMD] Admin command: ${command}`);
    }
}

/**
 * Parse fuel report fields from message body
 * Returns an object with department, driver, car, liters, amount, type, odometer
 */
function parseFuelFields(body) {
    const fields = {};
    const lines = body.split('\n').map(l => l.trim());
    
    for (const line of lines) {
        // Match patterns like "FIELD: value" or "FIELD - value" or "FIELD = value"
        const match = line.match(/^([A-Za-z\s]+)[:\-=]\s*(.+)$/);
        if (!match) continue;
        
        const key = match[1].trim().toUpperCase();
        const value = match[2].trim();
        
        if (['DEPARTMENT', 'DEPT', 'SECTION'].includes(key)) {
            fields.department = value.toUpperCase();
        } else if (['DRIVER', 'JINA'].includes(key)) {
            fields.driver = value;
        } else if (['CAR', 'REG', 'REG NO', 'VEHICLE', 'GARI'].includes(key)) {
            fields.car = value.toUpperCase();
        } else if (['LITERS', 'LITRES', 'LTRS', 'LITA'].includes(key)) {
            fields.liters = value.replace(/,/g, '');
        } else if (['AMOUNT', 'KSH', 'COST', 'PESA'].includes(key)) {
            fields.amount = value.replace(/,/g, '').replace(/KSH/gi, '').trim();
        } else if (['TYPE', 'FUEL', 'FUEL TYPE'].includes(key)) {
            fields.type = value.toUpperCase();
        } else if (['ODOMETER', 'MILEAGE', 'KM', 'ODO'].includes(key)) {
            fields.odometer = value.replace(/,/g, '');
        }
    }
    
    return fields;
}

/**
 * Format a professional confirmation message for successful fuel report capture
 */
function formatConfirmation(senderName, fields, datetime) {
    let msg = `[LOGGED] *FUEL REPORT LOGGED*\n\n`;
    
    if (fields.driver) msg += `Driver: ${fields.driver}\n`;
    if (fields.car) msg += `Vehicle: ${fields.car}\n`;
    if (fields.liters) msg += `Fuel: ${fields.liters} L`;
    if (fields.type) msg += ` (${fields.type})`;
    if (fields.liters) msg += `\n`;
    if (fields.amount) msg += `Amount: KSH ${parseFloat(fields.amount).toLocaleString()}\n`;
    if (fields.odometer) msg += `Odometer: ${parseInt(fields.odometer).toLocaleString()} km\n`;
    
    msg += `\n_${datetime} | ${senderName}_`;
    
    return msg;
}

// Rate limiting for group messages
let lastMessageTime = 0;
const MESSAGE_COOLDOWN_MS = 1000; // 1 second between messages

/**
 * Send a message to the target group with rate limiting
 */
async function sendGroupMessage(message) {
    if (!client || !isReady || !targetGroupId) {
        console.log('[WARN] Cannot send message: client not ready or no target group');
        return false;
    }
    
    // Rate limiting to prevent spam/bans
    const now = Date.now();
    const timeSinceLastMessage = now - lastMessageTime;
    if (timeSinceLastMessage < MESSAGE_COOLDOWN_MS) {
        await new Promise(resolve => setTimeout(resolve, MESSAGE_COOLDOWN_MS - timeSinceLastMessage));
    }
    
    try {
        await client.sendMessage(targetGroupId, message);
        lastMessageTime = Date.now();
        console.log('[SENT] Sent message to group');
        return true;
    } catch (error) {
        console.error('[ERROR] Error sending message:', error.message);
        return false;
    }
}

// Export for use by processor (via IPC or file-based communication)
function saveValidationError(carPlate, driverName, issue) {
    const errorFile = path.join(ROOT_DIR, 'data', 'validation_errors.json');
    const error = {
        timestamp: new Date().toISOString(),
        car: carPlate,
        driver: driverName,
        issue: issue,
        notified: false
    };
    
    let errors = [];
    if (fs.existsSync(errorFile)) {
        try {
            errors = JSON.parse(fs.readFileSync(errorFile, 'utf8'));
        } catch (e) {
            errors = [];
        }
    }
    errors.push(error);
    fs.writeFileSync(errorFile, JSON.stringify(errors, null, 2));
}

/**
 * Check for validation errors and notify the group
 */
async function checkAndNotifyErrors() {
    const errorFile = path.join(ROOT_DIR, 'data', 'validation_errors.json');
    
    if (!fs.existsSync(errorFile)) return;
    
    try {
        let errors = JSON.parse(fs.readFileSync(errorFile, 'utf8'));
        if (!Array.isArray(errors)) errors = [];
        
        const unnotified = errors.filter(e => !e.notified);
        
        // Limit to 3 notifications per cycle to prevent spam
        const toNotify = unnotified.slice(0, 3);
        
        for (const error of toNotify) {
            // Check if this is an approval request (tag admins) or validation error (tag sender)
            const isApprovalRequest = error.is_approval_request === true;
            
            let message;
            let mentionPhones = [];
            
            if (isApprovalRequest) {
                // Approval request - tag admins
                mentionPhones = await getGroupAdminPhones();
                
                // Build message with admin tags at the start
                const adminTags = mentionPhones.map(p => `@${p}`).join(' ');
                message = adminTags ? `${adminTags}\n\n${error.issue}` : error.issue;
            } else {
                // Validation error - tag the sender who made the mistake
                const senderPhone = error.sender_phone;
                
                if (senderPhone) {
                    mentionPhones = [senderPhone];
                    message = `@${senderPhone}\n\n` +
                        `[!] *FUEL REPORT ERROR*\n\n` +
                        `Driver: ${error.driver || 'Unknown'}\n` +
                        `Car: ${error.car || 'Unknown'}\n` +
                        `Issue: ${error.issue}\n\n` +
                        `Please resend with correct details.\n` +
                        `Type *!how* for guidance.`;
                } else {
                    message = `[!] *FUEL REPORT ERROR*\n\n` +
                        `Driver: ${error.driver || 'Unknown'}\n` +
                        `Car: ${error.car || 'Unknown'}\n` +
                        `Issue: ${error.issue}\n\n` +
                        `Please resend with correct details.\n` +
                        `Type *!how* for guidance.`;
                }
            }
            
            // Send with mentions if we have any
            let sent = false;
            if (mentionPhones.length > 0) {
                sent = await sendGroupMessageWithMentions(message, mentionPhones);
            } else {
                sent = await sendGroupMessage(message);
            }
            
            if (sent) {
                error.notified = true;
            }
        }
        
        // Clean up old notified errors (keep last 100)
        const recentErrors = errors.slice(-100);
        fs.writeFileSync(errorFile, JSON.stringify(recentErrors, null, 2));
        
    } catch (e) {
        console.error('Error checking validation errors:', e.message);
    }
}

/**
 * Check for confirmation messages from processor and send to group
 */
async function checkAndSendConfirmations() {
    const confirmFile = path.join(ROOT_DIR, 'data', 'confirmations.json');
    
    if (!fs.existsSync(confirmFile)) return;
    
    try {
        let confirmations = JSON.parse(fs.readFileSync(confirmFile, 'utf8'));
        if (!Array.isArray(confirmations)) confirmations = [];
        
        const unnotified = confirmations.filter(c => !c.notified);
        
        // Limit to 5 confirmations per cycle to prevent spam
        const toNotify = unnotified.slice(0, 5);
        
        for (const confirmation of toNotify) {
            if (confirmation.message) {
                if (await sendGroupMessage(confirmation.message)) {
                    confirmation.notified = true;
                    confirmation.notifiedAt = new Date().toISOString();
                }
            }
        }
        
        // Clean up old confirmations (keep last 200)
        const recentConfirmations = confirmations.slice(-200);
        fs.writeFileSync(confirmFile, JSON.stringify(recentConfirmations, null, 2));
        
    } catch (e) {
        console.error('Error checking confirmations:', e.message);
    }
}

/**
 * Initialize and start the WhatsApp client
 */
async function startClient() {
    console.log('\n' + '='.repeat(60));
    console.log('[START] WhatsApp Fuel Extractor - Starting...');
    console.log('='.repeat(60) + '\n');
    
    // Load and validate config
    config = loadConfig();
    const issues = validateConfig(config);
    
    if (issues.length > 0) {
        console.error('[ERROR] Configuration issues:');
        issues.forEach(issue => console.error(`   - ${issue}`));
        console.log('\n[INFO] Please edit config.json and restart.');
        process.exit(1);
    }
    
    console.log('[CONFIG] Configuration loaded:');
    console.log(`   Phone: ${config.whatsapp.phoneNumber || '(will be set via QR)'}`);
    console.log(`   Group: ${config.whatsapp.groupName}`);
    console.log(`   Output: ${config.output.excelFolder}/${config.output.excelFileName}`);
    console.log('');
    
    // Ensure directories exist
    [RAW_MESSAGES_PATH, SESSION_PATH].forEach(dir => {
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
    });
    
    // Create WhatsApp client
    const puppeteerConfig = {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    };
    
    // Add executable path if we found Chromium
    if (chromiumPath) {
        puppeteerConfig.executablePath = chromiumPath;
    }
    
    client = new Client({
        authStrategy: new LocalAuth({
            dataPath: SESSION_PATH
        }),
        webVersionCache: {
            type: 'remote',
            remotePath: 'https://raw.githubusercontent.com/nicpanoaea/nicpanoaea.github.io/refs/heads/master/nicpanoaea.github.io/nicpanoaea.html'
        },
        puppeteer: puppeteerConfig
    });
    
    // QR Code event - display for scanning
    client.on('qr', (qr) => {
        console.log('\n' + '='.repeat(60));
        console.log('[QR] SCAN THIS QR CODE WITH YOUR WHATSAPP:');
        console.log('='.repeat(60));
        qrcode.generate(qr, { small: true });
        console.log('='.repeat(60));
        console.log('Open WhatsApp > Settings > Linked Devices > Link a Device');
        console.log('='.repeat(60) + '\n');
    });
    
    // Ready event - client connected
    client.on('ready', async () => {
        isReady = true;
        console.log('\n[OK] WhatsApp client is ready!');
        
        // Get client info
        const info = client.info;
        console.log(`[CONNECTED] Connected as: ${info.pushname} (${info.wid.user})`);
        
        // Update config with phone number if not set
        if (!config.whatsapp.phoneNumber || config.whatsapp.phoneNumber !== info.wid.user) {
            config.whatsapp.phoneNumber = info.wid.user;
            fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
            console.log(`[CONFIG] Updated config.json with phone number: ${info.wid.user}`);
        }
        
        // Find target group
        targetGroupId = await findTargetGroup();
        
        if (targetGroupId) {
            console.log('\n[MONITOR] Now monitoring for fuel reports...');
            console.log('   Messages will be saved to: data/raw_messages/');
            console.log('   Press Ctrl+C to stop.\n');
            
            // Check for missed messages while offline
            const missedCount = await fetchMissedMessages(targetGroupId);
            
            // Send startup notification to the group
            let startupMsg = `[ONLINE] *FUEL EXTRACTOR ONLINE*\n\n` +
                `[STATUS] System connected and monitoring\n` +
                `[TIME] ${new Date().toLocaleString()}`;
            
            if (missedCount > 0) {
                startupMsg += `\n\n[INBOX] Processing ${missedCount} missed report(s)...`;
            }
            
            startupMsg += `\n\n_Type !how for fuel update guide_`;
            
            await sendGroupMessage(startupMsg);
            console.log('[NOTIFY] Startup notification sent to group');
        } else {
            console.log('\n[WARN] Continuing to monitor all groups for now...');
            console.log('   Edit config.json with correct group name and restart.\n');
        }
    });
    
    // Message event - handle ALL messages (including your own)
    // Using 'message_create' instead of 'message' to capture outgoing messages too
    client.on('message_create', async (msg) => {
        try {
            // Get chat info
            const chat = await msg.getChat();
            
            // Only process group messages
            if (!chat.isGroup) return;
            
            // If we have a target group, only process messages from it
            if (targetGroupId && chat.id._serialized !== targetGroupId) return;
            
            // Check for admin commands first (only from target group)
            if (isAdminCommand(msg.body)) {
                console.log(`\n[CMD] Admin command received: ${msg.body.split('\n')[0]}`);
                await handleAdminCommand(msg, msg.body);
                return;
            }
            
            // Check if it looks like a fuel report
            if (!isFuelReport(msg.body)) return;
            
            // Get sender info - handle own messages differently
            let senderName = 'Unknown';
            let senderPhone = '';
            
            try {
                if (msg.fromMe) {
                    // Message from ourselves
                    senderName = client.info.pushname || 'Me';
                    senderPhone = client.info.wid.user;
                } else {
                    // Message from someone else
                    const contact = await msg.getContact();
                    senderName = contact.pushname || contact.name || contact.number || 'Unknown';
                    senderPhone = contact.number || '';
                }
            } catch (contactError) {
                console.log('   (Could not get contact info, using fallback)');
                senderName = msg.author || msg._data.notifyName || 'Unknown';
                senderPhone = msg.author ? msg.author.split('@')[0] : '';
            }
            
            // Prepare message data
            const messageData = {
                id: msg.id._serialized,
                timestamp: msg.timestamp,
                datetime: new Date(msg.timestamp * 1000).toISOString(),
                groupName: chat.name,
                groupId: chat.id._serialized,
                senderPhone: senderPhone,
                senderName: senderName,
                body: msg.body,
                capturedAt: new Date().toISOString()
            };
            
            console.log(`\n[MSG] Fuel report from ${senderName} in "${chat.name}":`);
            console.log(`   ${msg.body.substring(0, 100)}${msg.body.length > 100 ? '...' : ''}`);
            
            // Save message - confirmation will be sent after processor validates
            saveMessage(messageData);
            console.log('   [SAVED] Saved for processing (confirmation pending validation)');
            
            // Update last processed timestamp
            updateLastProcessedTime(msg.timestamp);
            
        } catch (error) {
            console.error('[ERROR] Error processing message:', error.message);
        }
    });
    
    // Handle message edits - check for key field changes
    client.on('message_edit', async (msg, newBody, oldBody) => {
        try {
            // Get chat info
            const chat = await msg.getChat();
            
            // Only process group messages from target group
            if (!chat.isGroup) return;
            if (targetGroupId && chat.id._serialized !== targetGroupId) return;
            
            // Check if the edited message is now a fuel report
            if (!isFuelReport(newBody)) return;
            
            console.log(`\n[EDIT] Message edited in "${chat.name}":`);
            console.log(`   Old: ${oldBody.substring(0, 50)}...`);
            console.log(`   New: ${newBody.substring(0, 50)}...`);
            
            // Parse both old and new to detect key field changes
            const oldFields = parseFuelFields(oldBody);
            const newFields = parseFuelFields(newBody);
            
            // Check if key fields changed (driver, car, department, odometer, liters, amount, type)
            const keyFieldsChanged = [];
            if (oldFields.driver !== newFields.driver) keyFieldsChanged.push('DRIVER');
            if (oldFields.car !== newFields.car) keyFieldsChanged.push('CAR');
            if (oldFields.department !== newFields.department) keyFieldsChanged.push('DEPARTMENT');
            if (oldFields.odometer !== newFields.odometer) keyFieldsChanged.push('ODOMETER');
            if (oldFields.liters !== newFields.liters) keyFieldsChanged.push('LITERS');
            if (oldFields.amount !== newFields.amount) keyFieldsChanged.push('AMOUNT');
            if (oldFields.type !== newFields.type) keyFieldsChanged.push('TYPE');
            
            // Check if edit is within 10 minutes of original message
            const msgTime = new Date(msg.timestamp * 1000);
            const now = new Date();
            const minutesSinceOriginal = (now - msgTime) / (1000 * 60);
            const isWithinEditWindow = minutesSinceOriginal <= 10;
            
            // Find and update existing message file, or check if already processed
            const files = fs.readdirSync(RAW_MESSAGES_PATH);
            let found = false;
            
            for (const file of files) {
                if (!file.endsWith('.json') || file === '.gitkeep') continue;
                
                const filepath = path.join(RAW_MESSAGES_PATH, file);
                try {
                    const content = JSON.parse(fs.readFileSync(filepath, 'utf8'));
                    if (content.id === msg.id._serialized) {
                        // Message still pending - just update it
                        content.body = newBody;
                        content.editedAt = new Date().toISOString();
                        content.originalBody = oldBody;
                        fs.writeFileSync(filepath, JSON.stringify(content, null, 2));
                        console.log(`[SAVED] Updated existing message file: ${file}`);
                        found = true;
                        break;
                    }
                } catch (e) {
                    continue;
                }
            }
            
            // If not found in raw_messages, the original was already processed
            if (!found) {
                let senderName = 'Unknown';
                let senderPhone = '';
                
                try {
                    if (msg.fromMe) {
                        senderName = client.info.pushname || 'Me';
                        senderPhone = client.info.wid.user;
                    } else {
                        const contact = await msg.getContact();
                        senderName = contact.pushname || contact.name || contact.number || 'Unknown';
                        senderPhone = contact.number || '';
                    }
                } catch (e) {
                    senderName = msg.author || 'Unknown';
                }
                
                // If key fields changed and within edit window, require admin approval
                if (keyFieldsChanged.length > 0 && isWithinEditWindow) {
                    console.log(`[WARN] Key fields changed: ${keyFieldsChanged.join(', ')} - requires approval`);
                    
                    // Save to pending approvals
                    const pendingFile = path.join(ROOT_DIR, 'data', 'pending_approvals.json');
                    let approvals = [];
                    if (fs.existsSync(pendingFile)) {
                        try {
                            approvals = JSON.parse(fs.readFileSync(pendingFile, 'utf8'));
                        } catch (e) {}
                    }
                    
                    const approvalId = Math.random().toString(36).substring(2, 10);
                    
                    // Format datetime like the processor does: YYYY-MM-DD-HH-MM
                    const origMsgTime = new Date(msg.timestamp * 1000);
                    const originalDatetime = origMsgTime.getFullYear() + '-' +
                        String(origMsgTime.getMonth() + 1).padStart(2, '0') + '-' +
                        String(origMsgTime.getDate()).padStart(2, '0') + '-' +
                        String(origMsgTime.getHours()).padStart(2, '0') + '-' +
                        String(origMsgTime.getMinutes()).padStart(2, '0');
                    
                    approvals.push({
                        id: approvalId,
                        type: 'edit',
                        timestamp: new Date().toISOString(),
                        record: {
                            department: newFields.department || '',
                            driver: newFields.driver || '',
                            car: newFields.car || '',
                            liters: newFields.liters || '',
                            amount: newFields.amount || '',
                            type: newFields.type || '',
                            odometer: newFields.odometer || '',
                            sender: senderName
                        },
                        original_record: {
                            datetime: originalDatetime,
                            department: oldFields.department || '',
                            driver: oldFields.driver || '',
                            car: oldFields.car || '',
                            liters: oldFields.liters || '',
                            amount: oldFields.amount || '',
                            type: oldFields.type || '',
                            odometer: oldFields.odometer || ''
                        },
                        reason: `Edited ${keyFieldsChanged.join(', ')} within ${Math.round(minutesSinceOriginal)} min`,
                        status: 'pending',
                        notified: false,
                        originalMsgId: msg.id._serialized
                    });
                    
                    fs.writeFileSync(pendingFile, JSON.stringify(approvals, null, 2));
                    
                    // Build detailed comparison message
                    let issueMsg = `[EDIT] *MESSAGE EDIT DETECTED*\n`;
                    issueMsg += `----------------------------\n\n`;
                    issueMsg += `[TIME] *Time since original post:* ${Math.round(minutesSinceOriginal)} minutes\n`;
                    issueMsg += `[CHANGES] *Fields changed:* ${keyFieldsChanged.join(', ')}\n\n`;
                    
                    // Show detailed comparison for each changed field
                    issueMsg += `[DETAILS] *DETAILED CHANGES*\n`;
                    issueMsg += `----------------------------\n`;
                    
                    if (keyFieldsChanged.includes('DRIVER')) {
                        issueMsg += `[DRIVER] *DRIVER*\n`;
                        issueMsg += `   Before: ${oldFields.driver || 'N/A'}\n`;
                        issueMsg += `   After:  ${newFields.driver || 'N/A'}\n\n`;
                    }
                    if (keyFieldsChanged.includes('CAR')) {
                        issueMsg += `[CAR] *CAR*\n`;
                        issueMsg += `   Before: ${oldFields.car || 'N/A'}\n`;
                        issueMsg += `   After:  ${newFields.car || 'N/A'}\n\n`;
                    }
                    if (keyFieldsChanged.includes('DEPARTMENT')) {
                        issueMsg += `[DEPT] *DEPARTMENT*\n`;
                        issueMsg += `   Before: ${oldFields.department || 'N/A'}\n`;
                        issueMsg += `   After:  ${newFields.department || 'N/A'}\n\n`;
                    }
                    if (keyFieldsChanged.includes('ODOMETER')) {
                        const oldOdo = oldFields.odometer ? parseInt(oldFields.odometer.toString().replace(/,/g, '')) : 0;
                        const newOdo = newFields.odometer ? parseInt(newFields.odometer.toString().replace(/,/g, '')) : 0;
                        const odoDiff = newOdo - oldOdo;
                        issueMsg += `[ODO] *ODOMETER*\n`;
                        issueMsg += `   Before: ${oldOdo.toLocaleString()} km\n`;
                        issueMsg += `   After:  ${newOdo.toLocaleString()} km\n`;
                        issueMsg += `   Diff:   ${odoDiff >= 0 ? '+' : ''}${odoDiff.toLocaleString()} km\n\n`;
                    }
                    if (keyFieldsChanged.includes('LITERS')) {
                        const oldLiters = oldFields.liters ? parseFloat(oldFields.liters.toString().replace(/,/g, '')) : 0;
                        const newLiters = newFields.liters ? parseFloat(newFields.liters.toString().replace(/,/g, '')) : 0;
                        const litersDiff = newLiters - oldLiters;
                        issueMsg += `[FUEL] *LITERS*\n`;
                        issueMsg += `   Before: ${oldLiters.toFixed(1)} L\n`;
                        issueMsg += `   After:  ${newLiters.toFixed(1)} L\n`;
                        issueMsg += `   Diff:   ${litersDiff >= 0 ? '+' : ''}${litersDiff.toFixed(1)} L\n\n`;
                    }
                    if (keyFieldsChanged.includes('AMOUNT')) {
                        const oldAmount = oldFields.amount ? parseFloat(oldFields.amount.toString().replace(/,/g, '')) : 0;
                        const newAmount = newFields.amount ? parseFloat(newFields.amount.toString().replace(/,/g, '')) : 0;
                        const amountDiff = newAmount - oldAmount;
                        issueMsg += `[COST] *AMOUNT*\n`;
                        issueMsg += `   Before: KSH ${oldAmount.toLocaleString()}\n`;
                        issueMsg += `   After:  KSH ${newAmount.toLocaleString()}\n`;
                        issueMsg += `   Diff:   ${amountDiff >= 0 ? '+' : ''}KSH ${amountDiff.toLocaleString()}\n\n`;
                    }
                    if (keyFieldsChanged.includes('TYPE')) {
                        issueMsg += `[TYPE] *FUEL TYPE*\n`;
                        issueMsg += `   Before: ${oldFields.type || 'N/A'}\n`;
                        issueMsg += `   After:  ${newFields.type || 'N/A'}\n\n`;
                    }
                    
                    issueMsg += `----------------------------\n`;
                    issueMsg += `[ID] Approval ID: *${approvalId}*\n\n`;
                    issueMsg += `[OK] *!approve ${approvalId}* - Accept edit\n`;
                    issueMsg += `[X] *!reject ${approvalId}* - Keep original`;
                    
                    // Notify about pending approval
                    const errorFile = path.join(ROOT_DIR, 'data', 'validation_errors.json');
                    let errors = [];
                    if (fs.existsSync(errorFile)) {
                        try { errors = JSON.parse(fs.readFileSync(errorFile, 'utf8')); } catch (e) {}
                    }
                    
                    errors.push({
                        timestamp: new Date().toISOString(),
                        car: newFields.car || 'N/A',
                        driver: newFields.driver || senderName,
                        issue: issueMsg,
                        sender_phone: senderPhone,
                        is_approval_request: true,  // Tag admins, not sender
                        notified: false
                    });
                    
                    fs.writeFileSync(errorFile, JSON.stringify(errors, null, 2));
                    console.log(`[SAVED] Edit saved for approval: ${approvalId}`);
                    
                } else if (keyFieldsChanged.length > 0) {
                    // Key fields changed but outside edit window - save as new entry
                    console.log(`[WARN] Key fields changed after edit window - saving as new record`);
                    
                    const messageData = {
                        id: msg.id._serialized + '_edited_' + Date.now(),
                        originalId: msg.id._serialized,
                        timestamp: msg.timestamp,
                        datetime: new Date(msg.timestamp * 1000).toISOString(),
                        groupName: chat.name,
                        groupId: chat.id._serialized,
                        senderPhone: senderPhone,
                        senderName: senderName,
                        body: newBody,
                        originalBody: oldBody,
                        isEdit: true,
                        editedFields: keyFieldsChanged,
                        capturedAt: new Date().toISOString()
                    };
                    
                    saveMessage(messageData);
                    console.log('[SAVED] Saved as new record (edit after 10 min window)');
                } else {
                    // No key fields changed - just log
                    console.log('[INFO] Edit detected but no key fields changed - ignoring');
                }
            }
            
        } catch (error) {
            console.error('[ERROR] Error processing edited message:', error.message);
        }
    });
    
    // Check for confirmations and errors every 5 seconds (fast response)
    setInterval(async () => {
        await checkAndSendConfirmations();
        await checkAndNotifyErrors();
    }, 5 * 1000);
    
    // Authentication failure
    client.on('auth_failure', (msg) => {
        console.error('[ERROR] Authentication failed:', msg);
        console.log('[INFO] Clearing session and restarting...');
        clearSession();
        setTimeout(() => startClient(), 5000);
    });
    
    // Disconnected
    client.on('disconnected', (reason) => {
        console.log('[WARN] Client disconnected:', reason);
        isReady = false;
        targetGroupId = null;
        console.log('[INFO] Reconnecting in 10 seconds...');
        setTimeout(() => startClient(), 10000);
    });
    
    // Start the client
    console.log('[INIT] Initializing WhatsApp connection...');
    console.log('   (This may take a moment on first run)\n');
    
    try {
        await client.initialize();
    } catch (error) {
        console.error('[ERROR] Failed to initialize client:', error.message);
        console.log('[INFO] Retrying in 10 seconds...');
        setTimeout(() => startClient(), 10000);
    }
}

/**
 * Watch config.json for changes
 */
function watchConfig() {
    let lastConfig = JSON.stringify(loadConfig());
    
    const watcher = chokidar.watch(CONFIG_PATH, {
        persistent: true,
        ignoreInitial: true
    });
    
    watcher.on('change', async () => {
        console.log('\n[CONFIG] Config file changed, checking for updates...');
        
        try {
            const newConfig = loadConfig();
            const newConfigStr = JSON.stringify(newConfig);
            
            if (newConfigStr === lastConfig) {
                console.log('   No significant changes detected.');
                return;
            }
            
            const oldConfig = JSON.parse(lastConfig);
            lastConfig = newConfigStr;
            
            // Check if phone number changed (only if old phone was set - ignore initial auto-save)
            const oldPhone = oldConfig.whatsapp.phoneNumber || '';
            const newPhone = newConfig.whatsapp.phoneNumber || '';
            const phoneChanged = oldPhone !== '' && newPhone !== '' && oldPhone !== newPhone;
            
            // Check if group name changed
            const groupChanged = newConfig.whatsapp.groupName !== oldConfig.whatsapp.groupName;
            
            if (phoneChanged) {
                console.log('\n[INFO] Phone number changed! Starting fresh setup...');
                console.log(`   Old phone: ${oldPhone}`);
                console.log(`   New phone: ${newPhone}`);
                
                // Clear session for new phone
                clearSession();
                
                // Restart client
                if (client) {
                    await client.destroy();
                }
                config = newConfig;
                startClient();
                return;
            }
            
            if (groupChanged) {
                console.log('\n[INFO] Group name changed!');
                console.log(`   Old: ${oldConfig.whatsapp.groupName}`);
                console.log(`   New: ${newConfig.whatsapp.groupName}`);
                
                config = newConfig;
                
                // Find new target group
                if (isReady) {
                    targetGroupId = await findTargetGroup();
                }
                return;
            }
            
            // Other config changes
            config = newConfig;
            console.log('   Configuration updated.');
            
        } catch (error) {
            console.error('[ERROR] Error processing config change:', error.message);
        }
    });
    
    console.log('[WATCH] Watching config.json for changes...\n');
}

/**
 * Graceful shutdown
 */
process.on('SIGINT', async () => {
    console.log('\n\n[STOP] Shutting down gracefully...');
    
    // Update last processed time to now (so we know when system went offline)
    updateLastProcessedTime(Math.floor(Date.now() / 1000));
    console.log('[SAVED] Saved shutdown timestamp for missed message detection');
    
    // Send shutdown notification to the group before disconnecting
    if (client && isReady && targetGroupId) {
        try {
            const shutdownMsg = `[OFFLINE] *FUEL EXTRACTOR OFFLINE*\n\n` +
                `[PAUSE] System shutting down\n` +
                `[TIME] ${new Date().toLocaleString()}\n\n` +
                `_Messages sent now will be processed when system restarts_`;
            await client.sendMessage(targetGroupId, shutdownMsg);
            console.log('[NOTIFY] Shutdown notification sent to group');
            // Small delay to ensure message is sent
            await new Promise(resolve => setTimeout(resolve, 1000));
        } catch (error) {
            console.error('Warning: Could not send shutdown notification:', error.message);
        }
    }
    
    // Close health server
    healthServer.close(() => {
        console.log('[OK] Health server closed.');
    });
    
    if (client) {
        try {
            await client.destroy();
            console.log('[OK] WhatsApp client closed.');
        } catch (error) {
            console.error('Warning: Error closing client:', error.message);
        }
    }
    
    console.log('[BYE] Goodbye!\n');
    process.exit(0);
});

/**
 * Uncaught exception handler - prevents crashes
 */
process.on('uncaughtException', (error) => {
    console.error('[CRITICAL] Uncaught Exception:', error.message);
    console.error(error.stack);
    // Log to file for debugging
    const errorLog = path.join(ROOT_DIR, 'data', 'crash_log.txt');
    try {
        const entry = `[${new Date().toISOString()}] UNCAUGHT EXCEPTION: ${error.message}\n${error.stack}\n\n`;
        fs.appendFileSync(errorLog, entry);
    } catch (e) {}
    // Don't exit - try to continue running
});

/**
 * Unhandled rejection handler - prevents crashes from async errors
 */
process.on('unhandledRejection', (reason, promise) => {
    console.error('[CRITICAL] Unhandled Promise Rejection:', reason);
    // Log to file for debugging
    const errorLog = path.join(ROOT_DIR, 'data', 'crash_log.txt');
    try {
        const entry = `[${new Date().toISOString()}] UNHANDLED REJECTION: ${reason}\n\n`;
        fs.appendFileSync(errorLog, entry);
    } catch (e) {}
    // Don't exit - try to continue running
});

/**
 * Memory usage check - warn if memory is getting high
 */
setInterval(() => {
    const used = process.memoryUsage();
    const heapUsedMB = Math.round(used.heapUsed / 1024 / 1024);
    const heapTotalMB = Math.round(used.heapTotal / 1024 / 1024);
    
    if (heapUsedMB > 500) {
        console.warn(`[WARN] High memory usage: ${heapUsedMB}MB / ${heapTotalMB}MB`);
        // Force garbage collection if available
        if (global.gc) {
            console.log('[GC] Running garbage collection...');
            global.gc();
        }
    }
}, 60000); // Check every minute

// Main entry point
console.log('');
console.log('╔══════════════════════════════════════════════════════════╗');
console.log('║         WhatsApp Fuel Extractor v1.0.0                   ║');
console.log('║         Monitoring group messages for fuel reports       ║');
console.log('╚══════════════════════════════════════════════════════════╝');
console.log('');

watchConfig();
startClient();

/**
 * Health Check HTTP Server
 * Provides a simple endpoint for monitoring system health
 */
const HEALTH_PORT = process.env.HEALTH_PORT || 3000;

const healthServer = http.createServer((req, res) => {
    try {
        if (req.url === '/health' || req.url === '/') {
            let rawMessagesCount = 0;
            try {
                rawMessagesCount = fs.existsSync(RAW_MESSAGES_PATH) 
                    ? fs.readdirSync(RAW_MESSAGES_PATH).filter(f => f.endsWith('.json')).length 
                    : 0;
            } catch (e) {}
        
        const confirmationsPath = path.join(ROOT_DIR, 'data', 'confirmations.json');
        let pendingConfirmations = 0;
        if (fs.existsSync(confirmationsPath)) {
            try {
                const data = JSON.parse(fs.readFileSync(confirmationsPath, 'utf8'));
                pendingConfirmations = data.filter(c => !c.notified).length;
            } catch (e) {}
        }
        
        const errorsPath = path.join(ROOT_DIR, 'data', 'validation_errors.json');
        let pendingErrors = 0;
        if (fs.existsSync(errorsPath)) {
            try {
                const data = JSON.parse(fs.readFileSync(errorsPath, 'utf8'));
                pendingErrors = data.filter(e => !e.notified).length;
            } catch (e) {}
        }
        
        const uptimeSeconds = Math.floor(process.uptime());
        const hours = Math.floor(uptimeSeconds / 3600);
        const minutes = Math.floor((uptimeSeconds % 3600) / 60);
        const seconds = uptimeSeconds % 60;
        
        const healthData = {
            status: isReady ? 'healthy' : 'degraded',
            whatsapp: {
                connected: isReady,
                targetGroup: targetGroupId ? 'connected' : 'searching'
            },
            queue: {
                pendingMessages: rawMessagesCount,
                pendingConfirmations: pendingConfirmations,
                pendingErrors: pendingErrors
            },
            uptime: {
                seconds: uptimeSeconds,
                formatted: `${hours}h ${minutes}m ${seconds}s`
            },
            timestamp: new Date().toISOString()
        };
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(healthData, null, 2));
    } else {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Not found. Use /health endpoint.' }));
    }
    } catch (error) {
        console.error('Health check error:', error.message);
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ status: 'error', message: error.message }));
    }
});

healthServer.listen(HEALTH_PORT, () => {
    console.log(`[HEALTH] Health check server running on http://localhost:${HEALTH_PORT}/health`);
});
