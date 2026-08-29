import re
import logging

# Регулярное выражение для токенов Yandex.Music и Fernet ciphertexts (включая padding '=')
TOKEN_REGEX = re.compile(r'(y0_[A-Za-z0-9_=-]{15,}|enc:[A-Za-z0-9_=-]{15,})')


class TokenMaskingFilter(logging.Filter):
    """Глобальный фильтр маскирования чувствительных токенов и зашифрованных строк в логах."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = TOKEN_REGEX.sub('[TOKEN_REDACTED]', record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: TOKEN_REGEX.sub('[TOKEN_REDACTED]', str(v)) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        TOKEN_REGEX.sub('[TOKEN_REDACTED]', str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            pass
        return True
