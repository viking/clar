#!/usr/bin/env python

from __future__ import with_statement
from string import Template
import re, fnmatch, os, codecs, pickle

class Module(object):
    class Template(object):
        def __init__(self, module):
            self.module = module

        def _render_callback(self, cb):
            if not cb:
                return '{ NULL, NULL }'
            return '{ "%s", &%s }' % (cb['short_name'], cb['symbol'])

    class DeclarationTemplate(Template):
        def render(self):
            out = "\n".join("extern %s;" % cb['declaration'] for cb in self.module.callbacks)

            if self.module.initialize:
                out += "extern %s;\n" % self.module.initialize['declaration']

            if self.module.cleanup:
                out += "extern %s;\n" % self.module.cleanup['declaration']

            return out

    class CallbacksTemplate(Template):
        def render(self):
            out = "static const struct clar_func _clar_cb_%s[] = {\n" % self.module.name
            out += ",\n".join(self._render_callback(cb) for cb in self.module.callbacks)
            out += "\n};\n"
            return out

    class InfoTemplate(Template):
        def render(self):
            return Template(
            r"""{
                    "${clean_name}",
                    ${initialize},
                    ${cleanup},
                    ${cb_ptr}, ${cb_count}, ${enabled}
                }"""
            ).substitute(
                clean_name = self.module.clean_name(),
                initialize = self._render_callback(self.module.initialize),
                cleanup = self._render_callback(self.module.cleanup),
                cb_ptr = "_clar_cb_%s" % self.module.name,
                cb_count = len(self.module.callbacks),
                enabled = int(self.module.enabled)
            )

    def __init__(self, name):
        self.name = name

        self.mtime = 0
        self.enabled = True
        self.modified = False

    def clean_name(self):
        return self.name.replace("_", "::")

    def _skip_comments(self, text):
        SKIP_COMMENTS_REGEX = re.compile(
            r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
            re.DOTALL | re.MULTILINE)

        def _replacer(match):
            s = match.group(0)
            return "" if s.startswith('/') else s

        return re.sub(SKIP_COMMENTS_REGEX, _replacer, text)

    def parse(self, contents):
        TEST_FUNC_REGEX = r"^(void\s+(test_%s__(\w+))\(\s*void\s*\))\s*\{"

        contents = self._skip_comments(contents)
        regex = re.compile(TEST_FUNC_REGEX % self.name, re.MULTILINE)

        self.callbacks = []
        self.initialize = None
        self.cleanup = None

        for (declaration, symbol, short_name) in regex.findall(contents):
            data = {
                "short_name" : short_name,
                "declaration" : declaration,
                "symbol" : symbol
            }

            if short_name == 'initialize':
                self.initialize = data
            elif short_name == 'cleanup':
                self.cleanup = data
            else:
                self.callbacks.append(data)

        return self.callbacks != []

    def refresh(self, path):
        self.modified = False

        try:
            st = os.stat(path)

            # Not modified
            if st.st_mtime == self.mtime:
                return True

            self.modified = True
            self.mtime = st.st_mtime

            with open(path) as fp:
                raw_content = fp.read()

        except IOError:
            return False

        return self.parse(raw_content)

class TestSuite(object):

    def __init__(self, path):
        self.path = path

    def should_generate(self, path):
        if not os.path.isfile(path):
            return True

        if any(module.modified for module in self.modules.values()):
            return True

        return False

    def find_modules(self):
        modules = []
        for root, _, files in os.walk(self.path):
            module_root = root[len(self.path):]
            module_root = [c for c in module_root.split(os.sep) if c]

            tests_in_module = fnmatch.filter(files, "*.c")

            for test_file in tests_in_module:
                full_path = os.path.join(root, test_file)
                module_name = "_".join(module_root + [test_file[:-2]])

                modules.append((full_path, module_name))

        return modules

    def load_cache(self):
        path = os.path.join(self.path, '.clarcache')
        cache = {}

        try:
            fp = open(path)
            cache = pickle.load(fp)
            fp.close()
        except IOError:
            pass

        return cache

    def save_cache(self):
        path = os.path.join(self.path, '.clarcache')
        with open(path, 'w') as cache:
            pickle.dump(self.modules, cache)

    def load(self, force = False):
        module_data = self.find_modules()
        self.modules = {} if force else self.load_cache()

        for path, name in module_data:
            if name not in self.modules:
                self.modules[name] = Module(name)

            if not self.modules[name].refresh(path):
                del self.modules[name]

    def suite_count(self):
        return len(self.modules)

    def callback_count(self):
        return sum(len(module.callbacks) for module in self.modules.values())

    def write(self):
        output = os.path.join(self.path, 'clar.suite')

        if not self.should_generate(output):
            return False

        with open(output, 'w') as data:
            for module in self.modules.values():
                t = Module.DeclarationTemplate(module)
                data.write(t.render())

            for module in self.modules.values():
                t = Module.CallbacksTemplate(module)
                data.write(t.render())

            suites = "static struct clar_suite _clar_suites[] = {" + ','.join(
                Module.InfoTemplate(module).render() for module in self.modules.values()
            ) + "};"

            data.write(suites)

            data.write("static const size_t _clar_suite_count = %d;" % self.suite_count())
            data.write("static const size_t _clar_callback_count = %d;" % self.callback_count())

        suite.save_cache()
        return True

if __name__ == '__main__':
    from optparse import OptionParser

    parser = OptionParser()
    parser.add_option('-f', '--force', dest='force', default=False)

    options, args = parser.parse_args()

    for path in args or ['.']:
        suite = TestSuite(path)
        suite.load(options.force)
        if suite.write():
            print "Written `clar.suite` (%d suites)" % len(suite.modules)

