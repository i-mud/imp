Imp = Imp or {
  process = nil,
  buffer = "",
  lastStatus = nil,
  lastError = nil,
  session = nil,
  connected = false,
  focused = false,
  connection = 0,
  foreground = 0,
}

function Imp.sendFrame(frame)
  if not Imp.process or not Imp.process.isRunning() then
    return false
  end

  Imp.process.send(yajl.to_string(frame) .. "\n")
  return true
end

function Imp.sendActionResult(id, status)
  return Imp.sendFrame({
    type = "action-result",
    id = id,
    status = status,
  })
end

function Imp.handleAction(frame)
  if
    type(frame.id) ~= "string"
    or type(frame.command) ~= "string"
    or type(frame.context) ~= "table"
  then
    return
  end

  local context = frame.context
  local current =
    Imp.connected
    and Imp.focused
    and Imp.session ~= nil
    and context.session == Imp.session
    and context.foreground == Imp.foreground
    and context.connection == Imp.connection

  if not current then
    Imp.sendActionResult(frame.id, "rejected")
    return
  end

  -- This is the final context fence immediately before the client-specific
  -- write. Passing false bypasses Mudlet aliases.
  local ok = pcall(send, frame.command, false)

  Imp.sendActionResult(
    frame.id,
    ok and "forwarded" or "rejected"
  )
end

function Imp.onHelperOutput(chunk)
  Imp.buffer = Imp.buffer .. chunk

  while true do
    local newline = Imp.buffer:find("\n", 1, true)
    if not newline then
      return
    end

    local line = Imp.buffer:sub(1, newline - 1)
    Imp.buffer = Imp.buffer:sub(newline + 1)

    if line ~= "" then
      local ok, frame = pcall(yajl.to_value, line)
      if ok and type(frame) == "table" then
        if frame.type == "status" then
          Imp.lastStatus = frame
          Imp.session = frame.session
        elseif frame.type == "action" then
          Imp.handleAction(frame)
        elseif frame.type == "error" then
          Imp.lastError = frame
        end
      end
    end
  end
end

function Imp.gmcpFrame(packageName, payload)
  Imp.sendFrame({
    type = "gmcp",
    at = math.floor(getEpoch() * 1000),
    package = packageName,
    payload = payload or {},
  })
end

function Imp.start(helperPath)
  if Imp.process and Imp.process.isRunning() then
    Imp.process.close()
  end

  local _, _, connected = getConnectionInfo()
  local focused = hasFocus()

  Imp.buffer = ""
  Imp.lastStatus = nil
  Imp.lastError = nil
  Imp.session = nil
  Imp.connected = connected == true
  Imp.focused = focused == true
  Imp.connection = Imp.connected and 1 or 0
  Imp.foreground = Imp.focused and 1 or 0

  Imp.process = spawn(Imp.onHelperOutput, helperPath)

  Imp.sendFrame({
    type = "init",
    profile = getProfileName(),
    connected = Imp.connected,
    focused = Imp.focused,
  })
end

function Imp.stop()
  if Imp.process then
    Imp.process.close()
    Imp.process = nil
  end

  Imp.session = nil
end

registerNamedEventHandler("imp", "connected", "sysConnectionEvent", function()
  if not Imp.connected then
    Imp.connection = Imp.connection + 1
  end

  Imp.connected = true
  Imp.sendFrame({type = "connected"})
end)

registerNamedEventHandler("imp", "disconnected", "sysDisconnectionEvent", function()
  Imp.connected = false
  Imp.sendFrame({type = "disconnected"})
end)

registerNamedEventHandler("imp", "focus", "sysProfileFocusChangeEvent", function(_, focused)
  local nextFocused = focused == true

  if nextFocused and not Imp.focused then
    Imp.foreground = Imp.foreground + 1
  end

  Imp.focused = nextFocused
  Imp.sendFrame({type = "focus", focused = nextFocused})
end)

registerNamedEventHandler("imp", "protocol enabled", "sysProtocolEnabled", function(_, protocol)
  if protocol == "GMCP" then
    Imp.sendFrame({type = "protocol", name = "GMCP", enabled = true})
  end
end)

registerNamedEventHandler("imp", "protocol disabled", "sysProtocolDisabled", function(_, protocol)
  if protocol == "GMCP" then
    Imp.sendFrame({type = "protocol", name = "GMCP", enabled = false})
  end
end)

registerNamedEventHandler("imp", "status", "gmcp.Char.Status", function()
  Imp.gmcpFrame("Char.Status", gmcp.Char.Status)
end)

registerNamedEventHandler("imp", "vitals", "gmcp.Char.Vitals", function()
  Imp.gmcpFrame("Char.Vitals", gmcp.Char.Vitals)
end)

registerNamedEventHandler("imp", "exit", "sysExitEvent", function()
  Imp.stop()
end)
