"""
Evolution API Client - WhatsApp Integration

Handles all communication with Evolution API for:
- Sending messages to WhatsApp groups
- Receiving webhooks for incoming messages
- Managing instance connection status
- QR code retrieval

Evolution API: http://localhost:8080
API Docs: http://localhost:8080/docs
"""

import os
import json
import logging
import httpx
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from pathlib import Path

# Setup logging
logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_EVOLUTION_URL = "http://localhost:8080"
DEFAULT_API_KEY = "B6D711FCDE4D4FD5936544120E713976"


class EvolutionAPI:
    """
    Client for Evolution API v2.
    
    Handles WhatsApp messaging via Evolution API Docker instance.
    Thread-safe for use with FastAPI.
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        instance_name: Optional[str] = None,
        timeout: float = 30.0
    ):
        """
        Initialize Evolution API client.
        
        Args:
            base_url: Evolution API URL (default: http://localhost:8080)
            api_key: API authentication key
            instance_name: WhatsApp instance name
            timeout: Request timeout in seconds
        """
        self.base_url = (base_url or os.getenv("EVOLUTION_API_URL", DEFAULT_EVOLUTION_URL)).rstrip("/")
        self.api_key = api_key or os.getenv("EVOLUTION_API_KEY", DEFAULT_API_KEY)
        self.instance_name = instance_name or os.getenv("EVOLUTION_INSTANCE_NAME", "fuel-extractor")
        self.timeout = timeout
        
        # HTTP client with connection pooling
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "apikey": self.api_key,
                "Content-Type": "application/json"
            },
            timeout=self.timeout
        )
        
        # Async client for FastAPI
        self._async_client: Optional[httpx.AsyncClient] = None
    
    def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "apikey": self.api_key,
                    "Content-Type": "application/json"
                },
                timeout=self.timeout
            )
        return self._async_client
    
    async def close(self):
        """Close HTTP clients."""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
        self._client.close()
    
    # ==================== Instance Management ====================
    
    def get_instances(self) -> List[Dict]:
        """Get all WhatsApp instances."""
        try:
            response = self._client.get("/instance/fetchInstances")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get instances: {e}")
            return []
    
    async def get_instances_async(self) -> List[Dict]:
        """Get all WhatsApp instances (async)."""
        try:
            client = self._get_async_client()
            response = await client.get("/instance/fetchInstances")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get instances: {e}")
            return []
    
    def create_instance(
        self,
        instance_name: Optional[str] = None,
        webhook_url: Optional[str] = None,
        webhook_events: Optional[List[str]] = None
    ) -> Dict:
        """
        Create a new WhatsApp instance.
        
        Args:
            instance_name: Name for the instance
            webhook_url: URL to receive webhook events
            webhook_events: List of events to subscribe to
        """
        name = instance_name or self.instance_name
        
        # Default webhook events for fuel extractor
        if webhook_events is None:
            webhook_events = [
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE", 
                "MESSAGES_DELETE",
                "CONNECTION_UPDATE",
                "QRCODE_UPDATED"
            ]
        
        payload = {
            "instanceName": name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            "rejectCall": False,
            "groupsIgnore": False,
            "alwaysOnline": False,
            "readMessages": True,
            "readStatus": True,
            "syncFullHistory": False
        }
        
        # Add webhook configuration if provided
        if webhook_url:
            payload["webhook"] = {
                "url": webhook_url,
                "byEvents": True,
                "base64": False,
                "events": webhook_events
            }
        
        try:
            response = self._client.post("/instance/create", json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Created instance: {name}")
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.warning(f"Instance {name} may already exist")
                return {"instanceName": name, "status": "exists"}
            raise
        except Exception as e:
            logger.error(f"Failed to create instance: {e}")
            raise
    
    def get_instance_status(self, instance_name: Optional[str] = None) -> Dict:
        """Get connection status of an instance."""
        name = instance_name or self.instance_name
        try:
            response = self._client.get(f"/instance/connectionState/{name}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get instance status: {e}")
            return {"instance": name, "state": "unknown", "error": str(e)}
    
    async def get_instance_status_async(self, instance_name: Optional[str] = None) -> Dict:
        """Get connection status of an instance (async)."""
        name = instance_name or self.instance_name
        try:
            client = self._get_async_client()
            response = await client.get(f"/instance/connectionState/{name}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get instance status: {e}")
            return {"instance": name, "state": "unknown", "error": str(e)}
    
    def get_qrcode(self, instance_name: Optional[str] = None) -> Dict:
        """Get QR code for instance connection."""
        name = instance_name or self.instance_name
        try:
            response = self._client.get(f"/instance/connect/{name}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get QR code: {e}")
            return {"error": str(e)}
    
    def logout_instance(self, instance_name: Optional[str] = None) -> bool:
        """Logout/disconnect an instance."""
        name = instance_name or self.instance_name
        try:
            response = self._client.delete(f"/instance/logout/{name}")
            response.raise_for_status()
            logger.info(f"Logged out instance: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to logout instance: {e}")
            return False
    
    def delete_instance(self, instance_name: Optional[str] = None) -> bool:
        """Delete an instance completely."""
        name = instance_name or self.instance_name
        try:
            response = self._client.delete(f"/instance/delete/{name}")
            response.raise_for_status()
            logger.info(f"Deleted instance: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete instance: {e}")
            return False
    
    def set_webhook(
        self,
        webhook_url: str,
        instance_name: Optional[str] = None,
        events: Optional[List[str]] = None
    ) -> bool:
        """
        Configure webhook for an instance.
        
        Args:
            webhook_url: URL to receive events
            instance_name: Instance to configure
            events: List of events to subscribe to
        """
        name = instance_name or self.instance_name
        
        if events is None:
            events = [
                "MESSAGES_UPSERT",
                "MESSAGES_UPDATE",
                "CONNECTION_UPDATE"
            ]
        
        payload = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "webhookByEvents": True,
                "webhookBase64": False,
                "events": events
            }
        }
        
        try:
            response = self._client.put(f"/webhook/set/{name}", json=payload)
            response.raise_for_status()
            logger.info(f"Set webhook for {name}: {webhook_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            return False
    
    # ==================== Messaging ====================
    
    def send_text_message(
        self,
        to: str,
        text: str,
        instance_name: Optional[str] = None,
        mentions: Optional[List[str]] = None
    ) -> Dict:
        """
        Send a text message.
        
        Args:
            to: Recipient JID (e.g., "120363304885288170@g.us" for groups)
            text: Message text
            instance_name: Instance to use
            mentions: List of phone numbers to mention (for groups)
        """
        name = instance_name or self.instance_name
        
        payload = {
            "number": to,
            "text": text
        }
        
        # Add mentions if provided
        if mentions:
            mentioned_jids = [f"{m}@s.whatsapp.net" for m in mentions]
            payload["mentionsEveryOne"] = False
            payload["mentioned"] = mentioned_jids
        
        try:
            response = self._client.post(f"/message/sendText/{name}", json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Sent message to {to[:20]}...")
            return result
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return {"error": str(e)}
    
    async def send_text_message_async(
        self,
        to: str,
        text: str,
        instance_name: Optional[str] = None,
        mentions: Optional[List[str]] = None
    ) -> Dict:
        """Send a text message (async)."""
        name = instance_name or self.instance_name
        
        payload = {
            "number": to,
            "text": text
        }
        
        if mentions:
            mentioned_jids = [f"{m}@s.whatsapp.net" for m in mentions]
            payload["mentionsEveryOne"] = False
            payload["mentioned"] = mentioned_jids
        
        try:
            client = self._get_async_client()
            response = await client.post(f"/message/sendText/{name}", json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Sent message to {to[:20]}...")
            return result
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return {"error": str(e)}
    
    # ==================== Groups ====================
    
    def get_groups(self, instance_name: Optional[str] = None) -> List[Dict]:
        """Get all groups the instance is part of."""
        name = instance_name or self.instance_name
        try:
            response = self._client.get(f"/group/fetchAllGroups/{name}?getParticipants=false")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get groups: {e}")
            return []
    
    async def get_groups_async(self, instance_name: Optional[str] = None) -> List[Dict]:
        """Get all groups (async)."""
        name = instance_name or self.instance_name
        try:
            client = self._get_async_client()
            response = await client.get(f"/group/fetchAllGroups/{name}?getParticipants=false")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get groups: {e}")
            return []
    
    def find_group_by_name(self, group_name: str, instance_name: Optional[str] = None) -> Optional[Dict]:
        """Find a group by its name (case-insensitive)."""
        groups = self.get_groups(instance_name)
        group_name_lower = group_name.lower().strip()
        
        for group in groups:
            if group.get("subject", "").lower().strip() == group_name_lower:
                return group
        
        return None
    
    def get_group_participants(self, group_jid: str, instance_name: Optional[str] = None) -> List[Dict]:
        """Get participants of a group."""
        name = instance_name or self.instance_name
        try:
            response = self._client.get(f"/group/participants/{name}?groupJid={group_jid}")
            response.raise_for_status()
            data = response.json()
            return data.get("participants", [])
        except Exception as e:
            logger.error(f"Failed to get group participants: {e}")
            return []
    
    def get_group_admins(self, group_jid: str, instance_name: Optional[str] = None) -> List[str]:
        """Get admin phone numbers of a group."""
        participants = self.get_group_participants(group_jid, instance_name)
        admins = []
        
        for p in participants:
            if p.get("admin") in ["admin", "superadmin"]:
                # Extract phone number from JID
                jid = p.get("id", "")
                if "@" in jid:
                    phone = jid.split("@")[0]
                    admins.append(phone)
        
        return admins
    
    # ==================== Health Check ====================
    
    def health_check(self) -> Dict:
        """Check Evolution API health status."""
        try:
            # Try to fetch instances as a health check
            response = self._client.get("/instance/fetchInstances")
            
            # Get instance status if configured
            instance_status = None
            if self.instance_name:
                instance_status = self.get_instance_status()
            
            return {
                "status": "healthy",
                "api_url": self.base_url,
                "instance_name": self.instance_name,
                "instance_status": instance_status,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "api_url": self.base_url,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    async def health_check_async(self) -> Dict:
        """Check Evolution API health status (async)."""
        try:
            client = self._get_async_client()
            await client.get("/instance/fetchInstances")
            
            instance_status = None
            if self.instance_name:
                instance_status = await self.get_instance_status_async()
            
            return {
                "status": "healthy",
                "api_url": self.base_url,
                "instance_name": self.instance_name,
                "instance_status": instance_status,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "api_url": self.base_url,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    # ==================== Message History Fetch ====================
    
    def fetch_messages(self, chat_id: str, count: int = 50) -> List[Dict]:
        """
        Fetch message history from a chat.
        
        Args:
            chat_id: Chat JID (e.g., "120363304885288170@g.us" for groups)
            count: Number of messages to fetch (default: 50)
            
        Returns:
            List of message objects
        """
        try:
            response = self._client.post(
                f"/chat/fetchMessages/{self.instance_name}",
                json={
                    "where": {
                        "key": {
                            "remoteJid": chat_id
                        }
                    },
                    "limit": count
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                messages = data if isinstance(data, list) else data.get('messages', data.get('data', []))
                logger.info(f"[HISTORY] Fetched {len(messages)} messages from {chat_id}")
                return messages
            else:
                logger.warning(f"[HISTORY] Failed to fetch messages: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"[HISTORY] Error fetching messages: {e}")
            return []
    
    async def fetch_messages_async(self, chat_id: str, count: int = 50) -> List[Dict]:
        """
        Async version - Fetch message history from a chat.
        
        Args:
            chat_id: Chat JID (e.g., "120363304885288170@g.us" for groups)
            count: Number of messages to fetch (default: 50)
            
        Returns:
            List of message objects
        """
        try:
            client = self._get_async_client()
            response = await client.post(
                f"/chat/fetchMessages/{self.instance_name}",
                json={
                    "where": {
                        "key": {
                            "remoteJid": chat_id
                        }
                    },
                    "limit": count
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                messages = data if isinstance(data, list) else data.get('messages', data.get('data', []))
                logger.info(f"[HISTORY] Fetched {len(messages)} messages from {chat_id}")
                return messages
            else:
                logger.warning(f"[HISTORY] Failed to fetch messages: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"[HISTORY] Error fetching messages: {e}")
            return []


# ==================== Webhook Event Parsing ====================

def parse_webhook_event(payload: Dict) -> Dict:
    """
    Parse incoming webhook event from Evolution API.
    
    Returns normalized event data with:
    - event_type: Type of event (message, status, qrcode, etc.)
    - instance: Instance name
    - data: Event-specific data
    """
    event_type = payload.get("event", "unknown")
    instance = payload.get("instance", "")
    data = payload.get("data", {})
    
    result = {
        "event_type": event_type,
        "instance": instance,
        "timestamp": datetime.now().isoformat(),
        "raw": payload
    }
    
    if event_type == "messages.upsert":
        # New message received
        message = data.get("message", {})
        key = data.get("key", {})
        
        result["data"] = {
            "message_id": key.get("id", ""),
            "from_me": key.get("fromMe", False),
            "remote_jid": key.get("remoteJid", ""),
            "participant": key.get("participant", ""),  # In groups, this is the sender
            "push_name": data.get("pushName", ""),
            "message_type": data.get("messageType", ""),
            "text": extract_message_text(message),
            "timestamp": data.get("messageTimestamp", 0),
            "is_group": "@g.us" in key.get("remoteJid", "")
        }
    
    elif event_type == "messages.update":
        # Message status update (delivered, read, etc.)
        result["data"] = {
            "message_id": data.get("key", {}).get("id", ""),
            "status": data.get("update", {}).get("status", ""),
            "remote_jid": data.get("key", {}).get("remoteJid", "")
        }
    
    elif event_type == "connection.update":
        # Connection status change
        result["data"] = {
            "state": data.get("state", ""),
            "status_reason": data.get("statusReason", 0)
        }
    
    elif event_type == "qrcode.updated":
        # QR code updated
        result["data"] = {
            "qrcode": data.get("qrcode", {}).get("base64", "")
        }
    
    else:
        result["data"] = data
    
    return result


def extract_message_text(message: Dict) -> str:
    """Extract text content from various message types."""
    # Direct text message
    if "conversation" in message:
        return message["conversation"]
    
    # Extended text message
    if "extendedTextMessage" in message:
        return message["extendedTextMessage"].get("text", "")
    
    # Image/video with caption
    for media_type in ["imageMessage", "videoMessage", "documentMessage"]:
        if media_type in message:
            return message[media_type].get("caption", "")
    
    # Button response
    if "buttonsResponseMessage" in message:
        return message["buttonsResponseMessage"].get("selectedButtonId", "")
    
    # List response
    if "listResponseMessage" in message:
        return message["listResponseMessage"].get("title", "")
    
    return ""


def is_fuel_report(text: str) -> bool:
    """Check if message text is a fuel report."""
    if not text:
        return False
    
    text_upper = text.upper().strip()
    
    # Must start with "FUEL UPDATE"
    if not text_upper.startswith("FUEL UPDATE"):
        return False
    
    # Check for at least 2 fuel-related keywords
    keywords = ["DRIVER", "CAR", "LITERS", "LITRES", "AMOUNT", "TYPE", "ODOMETER", "KSH", "DIESEL", "PETROL"]
    matches = sum(1 for kw in keywords if kw in text_upper)
    
    return matches >= 2


def is_admin_command(text: str) -> bool:
    """Check if message is an admin command."""
    if not text:
        return False
    return text.strip().startswith("!")


# ==================== Singleton Instance ====================

_evolution_client: Optional[EvolutionAPI] = None


def get_evolution_client() -> EvolutionAPI:
    """Get or create singleton Evolution API client."""
    global _evolution_client
    if _evolution_client is None:
        _evolution_client = EvolutionAPI()
    return _evolution_client


async def close_evolution_client():
    """Close the singleton client."""
    global _evolution_client
    if _evolution_client:
        await _evolution_client.close()
        _evolution_client = None
