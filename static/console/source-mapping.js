/* Ordered provider IDs; each XMLTV file is a separate selectable source. */
window.EpgSourceMapping = (() => {
  const source = key => key.split('#', 1)[0];
  const key = (provider, identifier) => {
    if (provider !== 'xmltv') return provider;
    if (!/^[1-9]\d*:[A-Za-z0-9_+/-]+={0,2}$/.test(identifier))
      throw new Error('XMLTV ID 格式应为 来源编号:频道编码，请从来源目录选择。');
    return `xmltv#${identifier.split(':', 1)[0]}`;
  };
  const parse = value => {
    const result = new Map();
    const rest = value.replace(/<(\w+):([^<>]+)>/g, (_, provider, identifier) => {
      identifier = identifier.trim();
      if (!identifier) throw new Error('来源 ID 不能为空。');
      const identity = key(provider, identifier);
      if (result.has(identity) && result.get(identity) !== identifier)
        throw new Error(provider === 'xmltv' ? '同一 XMLTV 文件只能保留一个频道 ID；不同文件可同时选择。' : '同一来源只能保留一个 ID。');
      result.set(identity, identifier);
      return '';
    });
    if (rest.trim()) throw new Error('请先修正来源 ID 格式：<来源:ID>。');
    return result;
  };
  const text = mapping => {
    const value = [...mapping].map(([identity, identifier]) => `<${source(identity)}:${identifier}>`).join('');
    if (value.length > 300) throw new Error('来源 ID 超过 300 字符，请减少来源。');
    return value;
  };
  const first = (mapping, provider) => [...mapping.keys()].find(identity => source(identity) === provider);
  const promote = (mapping, provider, identifier) => {
    const identity = identifier ? key(provider, identifier) : (mapping.has(provider) ? provider : first(mapping, provider));
    if (!identity || !mapping.has(identity)) return new Map(mapping);
    return new Map([[identity, mapping.get(identity)], ...[...mapping].filter(([item]) => item !== identity)]);
  };
  return {source, key, parse, text, first, promote,
    has: (mapping, provider, identifier) => mapping.get(key(provider, identifier)) === identifier};
})();
