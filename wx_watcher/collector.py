"""Compatibility constructor for the composed message collector."""

from .application.collection import MessageCollector


class Collector(MessageCollector):
    def __init__(self, config, store, *, source=None, clock=None):
        from .composition import build_reader_dependencies

        super().__init__(
            config,
            store,
            **build_reader_dependencies(config, source=source, clock=clock),
        )
