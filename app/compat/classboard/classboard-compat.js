/* Platform features missing in older Android WebView. Loaded before app scripts. */
(function () {
  'use strict';
  function install(target, key, value) {
    if (typeof target[key] === 'undefined') {
      Object.defineProperty(target, key, { configurable: true, writable: true, value: value });
    }
  }
  install(Object, 'entries', function (object) {
    return Object.keys(object).map(function (key) { return [key, object[key]]; });
  });
  install(Object, 'values', function (object) {
    return Object.keys(object).map(function (key) { return object[key]; });
  });
  install(Object, 'fromEntries', function (entries) {
    var object = {};
    entries.forEach(function (entry) {
      Object.defineProperty(object, entry[0], {
        value: entry[1], enumerable: true, writable: true, configurable: true
      });
    });
    return object;
  });
  install(Array.prototype, 'flatMap', function (mapper, context) {
    return [].concat.apply([], Array.prototype.map.call(this, mapper, context));
  });
  install(String.prototype, 'padStart', function (length, padding) {
    var text = String(this);
    var fill = padding === undefined ? ' ' : String(padding);
    var count = Math.floor(length) - text.length;
    return count > 0 && fill ? fill.repeat(Math.ceil(count / fill.length)).slice(0, count) + text : text;
  });
  function fragment(nodes) {
    var result = document.createDocumentFragment();
    Array.prototype.forEach.call(nodes, function (node) {
      result.appendChild(node instanceof Node ? node : document.createTextNode(String(node)));
    });
    return result;
  }
  install(Element.prototype, 'append', function () { this.appendChild(fragment(arguments)); });
  install(Element.prototype, 'prepend', function () { this.insertBefore(fragment(arguments), this.firstChild); });
  install(Element.prototype, 'replaceChildren', function () {
    var children = fragment(arguments);
    while (this.firstChild) this.removeChild(this.firstChild);
    this.appendChild(children);
  });
  install(NodeList.prototype, 'forEach', Array.prototype.forEach);
  install(Blob.prototype, 'text', function () {
    var blob = this;
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(reader.error); };
      reader.readAsText(blob);
    });
  });
  if (!('srcObject' in HTMLMediaElement.prototype) && typeof URL.createObjectURL === 'function') {
    Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', {
      configurable: true,
      set: function (stream) {
        var media = this;
        var url = URL.createObjectURL(stream);
        function releaseUrl() {
          media.removeEventListener('loadedmetadata', releaseUrl);
          media.removeEventListener('error', releaseUrl);
          URL.revokeObjectURL(url);
        }
        media.addEventListener('loadedmetadata', releaseUrl);
        media.addEventListener('error', releaseUrl);
        media.src = url;
      }
    });
  }
})();
