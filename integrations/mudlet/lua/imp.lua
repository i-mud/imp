Imp = Imp or {
  process = nil,
  buffer = "",
  lastStatus = nil,
}

function Imp.sendFrame(frame)
  if not Imp.process or not Imp.process.isRunning() then
    return false
  end

  Imp.process.send(yajl.to_string(frame) .. "\n")
  return true
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
        Imp.lastStatus = frame
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

  Imp.buffer = ""
  Imp.lastStatus = nil
  Imp.process = spawn(Imp.onHelperOutput, helperPath)

  local _, _, connected = getConnectionInfo()

  Imp.sendFrame({
    type = "init",
    profile = getProfileName(),
    connected = connected == true,
    focused = hasFocus(),
  })
end

function Imp.stop()
  if Imp.process then
    Imp.process.close()
    Imp.process = nil
  end
end

registerNamedEventHandler("imp", "connected", "sysConnectionEvent", function()
  Imp.sendFrame({type = "connected"})
end)

registerNamedEventHandler("imp", "disconnected", "sysDisconnectionEvent", function()
  Imp.sendFrame({type = "disconnected"})
end)

registerNamedEventHandler("imp", "focus", "sysProfileFocusChangeEvent", function(_, focused)
  Imp.sendFrame({type = "focus", focused = focused == true})
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
