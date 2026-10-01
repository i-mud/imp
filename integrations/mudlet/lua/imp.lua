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
  Imp.refreshFocus()

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

function Imp.setFocus(focused)
  local nextFocused = focused == true

  if nextFocused == Imp.focused then
    return false
  end

  if nextFocused then
    Imp.foreground = Imp.foreground + 1
  end

  Imp.focused = nextFocused
  Imp.sendFrame({
    type = "focus",
    focused = nextFocused,
  })

  return true
end

function Imp.refreshFocus()
  -- hasFocus() also becomes false when Mudlet itself loses OS focus.
  -- That does not mean this profile stopped being Mudlet's foreground
  -- profile. Only sysProfileFocusChangeEvent is authoritative for
  -- demotion.
  if hasFocus() == true and not Imp.focused then
    return Imp.setFocus(true)
  end

  return false
end

function Imp.gmcpFrame(packageName, payload)
  Imp.refreshFocus()

  Imp.sendFrame({
    type = "gmcp",
    at = math.floor(getEpoch() * 1000),
    package = packageName,
    payload = payload or {},
  })
end

function Imp.defaultHelperPath()
  -- Explicit override is useful for development and unusual installations.
  local override = os.getenv("IMP_MUDLET_HELPER")
  if override and override ~= "" then
    local attributes = lfs.attributes(override)
    if attributes and attributes.mode == "file" then
      return override
    end
  end

  local roots = {}

  -- Windows production location. The Imp desktop installation provisions
  -- the native helper here once for all Mudlet profiles.
  local localAppData = os.getenv("LOCALAPPDATA")
  if localAppData and localAppData ~= "" then
    table.insert(roots, localAppData .. "/Imp/mudlet")
  end

  -- Linux production locations.
  local xdgDataHome = os.getenv("XDG_DATA_HOME")
  if xdgDataHome and xdgDataHome ~= "" then
    table.insert(roots, xdgDataHome .. "/imp/mudlet")
  end

  local home = os.getenv("HOME")
  if home and home ~= "" then
    table.insert(roots, home .. "/.local/share/imp/mudlet")
    table.insert(roots, home .. "/Library/Application Support/Imp/mudlet")
  end

  -- Compatibility with the initial package-bundled helper spike.
  table.insert(roots, getMudletHomeDir() .. "/Imp")

  for _, root in ipairs(roots) do
    local candidates = {
      root .. "/imp-mudlet-helper.exe",
      root .. "/imp-mudlet-helper",
    }

    for _, path in ipairs(candidates) do
      local attributes = lfs.attributes(path)
      if attributes and attributes.mode == "file" then
        return path
      end
    end
  end

  return nil
end

function Imp.start(helperPath, ...)
  if Imp.process and Imp.process.isRunning() then
    Imp.process.close()
  end

  Imp.buffer = ""
  Imp.lastStatus = nil
  Imp.lastError = nil
  Imp.session = nil

  local resolvedHelperPath = helperPath
  if resolvedHelperPath == nil or resolvedHelperPath == "" then
    resolvedHelperPath = Imp.defaultHelperPath()
  end

  if resolvedHelperPath == nil then
    Imp.lastError = {
      type = "error",
      protocol = 1,
      code = "helper_not_found",
    }
    return false
  end

  local _, _, connected = getConnectionInfo()
  local focused = hasFocus()

  Imp.connected = connected == true
  Imp.focused = focused == true
  Imp.connection = Imp.connected and 1 or 0
  Imp.foreground = Imp.focused and 1 or 0

  local ok, process = pcall(
    spawn,
    Imp.onHelperOutput,
    resolvedHelperPath,
    ...
  )

  if not ok then
    Imp.lastError = {
      type = "error",
      protocol = 1,
      code = "helper_spawn_failed",
      detail = tostring(process),
    }
    return false
  end

  Imp.process = process

  Imp.sendFrame({
    type = "init",
    profile = getProfileName(),
    connected = Imp.connected,
    focused = Imp.focused,
  })

  -- Package/module scripts can execute before Mudlet has settled profile
  -- focus. Reconcile once on the next event-loop turn.
  tempTimer(0, function()
    if Imp.process and Imp.process.isRunning() then
      Imp.refreshFocus()
    end
  end)

  return true
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
  Imp.refreshFocus()
end)

registerNamedEventHandler("imp", "disconnected", "sysDisconnectionEvent", function()
  Imp.connected = false
  Imp.sendFrame({type = "disconnected"})
end)

registerNamedEventHandler("imp", "focus", "sysProfileFocusChangeEvent", function(_, focused)
  Imp.setFocus(focused == true)
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
