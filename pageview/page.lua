-- pandoc filter of pageview: make the page inert before it reaches the viewer.
-- Raw HTML shows as text, event-handler attributes (onclick=…) are dropped, and links and images may only point to
-- http(s), mailto, data images, or the routes of the server (pageview.py makes relative paths into /file and /go).
-- The Content-Security-Policy of the viewer is the second line of defence.
-- It also removes the inline spans that the sourcepos extension adds around each word. The blocks keep data-pos, so
-- the viewer knows the source line of each block.

local function scheme(u)
  return (u:gsub("[%c%s]", "")):lower():match("^([%a][%w+.-]*):")
end

local function safe_target(u, image)
  local s = scheme(u)
  if not s then
    if u:match("^%s*//") then return "" end  -- a link to another host without a scheme
    return u
  end
  if s == "http" or s == "https" or (s == "mailto" and not image) then
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
    local k = kv[1]:lower()
    if not k:match("^on") and k ~= "style" and k ~= "srcdoc" and k ~= "formaction" then
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
    Span = function(el)
      if el.attributes["wrapper"] then  -- a sourcepos span around a word: keep the word only
        return el.content
      end
    end,
  },
  {
    Link = function(el)
      if safe_target(el.target, false) == "" then
        return pandoc.Span(el.content, el.attr)  -- keep the text, drop the link
      end
      if scheme(el.target) then
        el.attributes["target"] = "_blank"
        el.attributes["rel"] = "noopener noreferrer"
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
