import React, { useState, useEffect, useRef } from 'react'

const ChatInterface = () => {
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [targetEndpoint, setTargetEndpoint] = useState('task-generator')
  const [isConnected, setIsConnected] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef(null)
  const eventSourceRef = useRef(null)

  useEffect(() => {
    // Connect to SSE stream
    const connectSSE = () => {
      const eventSource = new EventSource('/chat/stream')
      eventSourceRef.current = eventSource

      eventSource.onopen = () => {
        setIsConnected(true)
        console.log('SSE connected')
      }

      eventSource.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          
          // Ignore keepalive messages
          if (message.type === 'keepalive') return
          
          setMessages(prev => {
            // Avoid duplicates
            if (prev.some(m => m.id === message.id)) return prev
            return [...prev, message]
          })
        } catch (error) {
          console.error('Error parsing SSE message:', error)
        }
      }

      eventSource.onerror = () => {
        setIsConnected(false)
        console.log('SSE connection error, reconnecting...')
        
        // Reconnect after 3 seconds
        setTimeout(() => {
          if (eventSourceRef.current?.readyState === EventSource.CLOSED) {
            connectSSE()
          }
        }, 3000)
      }
    }

    connectSSE()

    // Load chat history
    fetch('/chat/history')
      .then(res => res.json())
      .then(data => setMessages(data.messages || []))
      .catch(console.error)

    // Cleanup on unmount
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }
    }
  }, [])

  useEffect(() => {
    // Scroll to bottom when new messages arrive
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!inputValue.trim() || isLoading) return

    setIsLoading(true)
    
    try {
      const response = await fetch('/chat/send', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content: inputValue,
          target_endpoint: targetEndpoint
        })
      })

      if (response.ok) {
        setInputValue('')
      } else {
        const error = await response.json()
        console.error('Error sending message:', error)
      }
    } catch (error) {
      console.error('Network error:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const formatTimestamp = (timestamp) => {
    return new Date(timestamp).toLocaleTimeString()
  }

  const getMessageStyle = (message) => {
    const baseStyle = "message"
    if (message.type === 'user') return `${baseStyle} user-message`
    if (message.type === 'system') return `${baseStyle} system-message`
    return `${baseStyle} agent-message`
  }

  const getSourceBadge = (message) => {
    if (message.type === 'user') return '👤 You'
    if (message.type === 'system') return '⚠️ System'
    if (message.source) return `🤖 ${message.source}`
    return '🤖 Agent'
  }

  return (
    <div className="chat-container">
      <div className="chat-header">
        <h1>🦜 LangGraph Chat</h1>
        <div className="header-controls">
          <select 
            value={targetEndpoint} 
            onChange={(e) => setTargetEndpoint(e.target.value)}
            className="endpoint-selector"
          >
            <option value="task-generator">📝 Task Generator</option>
            <option value="task-solver">🔧 Task Solver</option>
            <option value="agent-comms">💬 Agent Comms</option>
          </select>
          <div className={`connection-status ${isConnected ? 'connected' : 'disconnected'}`}>
            {isConnected ? '🟢 Connected' : '🔴 Disconnected'}
          </div>
        </div>
      </div>

      <div className="messages-container">
        {messages.length === 0 ? (
          <div className="empty-state">
            <p>👋 Welcome to LangGraph Chat!</p>
            <p>Send a message to get started.</p>
          </div>
        ) : (
          messages.map((message) => (
            <div key={message.id} className={getMessageStyle(message)}>
              <div className="message-header">
                <span className="source-badge">{getSourceBadge(message)}</span>
                <span className="timestamp">{formatTimestamp(message.timestamp)}</span>
              </div>
              <div className="message-content">
                {message.content}
              </div>
              {message.target_endpoint && message.type === 'user' && (
                <div className="message-target">
                  📤 Sent to: {message.target_endpoint}
                </div>
              )}
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-container">
        <div className="input-wrapper">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder={`Message ${targetEndpoint}...`}
            disabled={!isConnected || isLoading}
            rows={1}
            className="message-input"
          />
          <button 
            onClick={sendMessage} 
            disabled={!isConnected || isLoading || !inputValue.trim()}
            className="send-button"
          >
            {isLoading ? '⏳' : '📤'}
          </button>
        </div>
        <div className="input-info">
          Press Enter to send • Shift+Enter for new line
        </div>
      </div>
    </div>
  )
}

export default ChatInterface