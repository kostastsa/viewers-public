-- pandoc filter: make a document inert before it reaches the viewer.
-- Raw HTML is shown as text, event-handler attributes (onclick=…) are dropped,
-- and links/images may only point to http(s), mailto, data images or relative paths.
-- The viewer's Content-Security-Policy is the second line of defence.

local function scheme(u)
  return (u:gsub("[%c%s]", "")):lower():match("^([%a][%w+.-]*):")
end

local function safe_target(u, image)
  local s = scheme(u)
  if not s or s == "http" or s == "https" or (s == "mailto" and not image) then
    return u
  end
  if image and u:lower():match("^%s*data:image/") then
    return u
  end
  return ""
end

local function clean_attr(el)
  if not el.attr then
    return nil
  end
  local kept = {}
  for _, kv in ipairs(el.attr.attributes) do
    if not kv[1]:lower():match("^on") then
      table.insert(kept, kv)
    end
  end
  el.attr.attributes = kept
  return el
end

return {
  {
    RawInline = function(el)
      return pandoc.Str(el.text)
    end,
    RawBlock = function(el)
      return pandoc.CodeBlock(el.text)
    end,
  },
  {
    Link = function(el)
      if safe_target(el.target, false) == "" then
        return pandoc.Span(el.content, el.attr)  -- keep the text, drop the link
      end
      return clean_attr(el)
    end,
    Image = function(el)
      el.src = safe_target(el.src, true)
      return clean_attr(el)
    end,
  },
  {
    Inline = clean_attr,
    Block = clean_attr,
  },
}
