#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu
# pylint: disable=cyclic-import

import datetime
import threading
import logging

from zimscraperlib.zim.creator import Creator
from zimscraperlib.logging import getLogger as lib_getLogger

from ..constants import NAME


class Global:
    """Shared context accross all scraper components"""

    class DatabaseException(Exception):
        pass

    debug = False
    logger = lib_getLogger(
        NAME,
        level=logging.INFO,
        log_format="[%(threadName)s::%(asctime)s] %(levelname)s:%(message)s",
    )
    conf = None

    site = None
    progresser = None

    database = None
    creator = None
    imager = None
    renderer = None
    rewriter = None
    lock = threading.Lock()

    @staticmethod
    def set_debug(value):
        Global.debug = value
        level = logging.DEBUG if value else logging.INFO
        Global.logger.setLevel(level)
        for handler in Global.logger.handlers:
            handler.setLevel(level)

    @staticmethod
    def init(site=None):
        from .progress import Progresser

        Global.progresser = Progresser()
        Global.site = site

    @staticmethod
    def setup():
        # order matters are there are references between them

        from .database import get_database

        try:
            Global.database = get_database()
        except Exception as exc:
            raise Global.DatabaseException(exc)

        # all tasks added to a bound queue processed by workers
        from .executor import SotokiExecutor

        Global.executor = SotokiExecutor(
            queue_size=Global.conf.nb_threads,
            nb_workers=int(Global.conf.nb_threads / 2),
        )

        # images handled on a different, unbounded queue
        Global.img_executor = SotokiExecutor(
            queue_size=Global.conf.nb_threads * 100,
            nb_workers=max([2, int(Global.conf.nb_threads / 2)]),
            prefix="IMG-T-",
        )

        from .imager import Imager

        Global.imager = Imager()

        from .html import Rewriter

        Global.rewriter = Rewriter()

        from ..renderer import Renderer

        Global.renderer = Renderer()

        Global.creator = Creator(
            filename=Global.conf.output_dir.joinpath(Global.conf.fname),
            main_path="questions",
            favicon_path="illustration",
            language="eng",
            title=Global.conf.title,
            description=Global.conf.description,
            creator=Global.conf.author,
            publisher=Global.conf.publisher,
            name=Global.conf.name,
            tags=";".join(Global.conf.tags),
            date=datetime.date.today(),
        ).config_nbworkers(int(Global.conf.nb_threads / 2))


class GlobalMixin:
    @property
    def conf(self):
        return Global.conf

    @property
    def site(self):
        return Global.site

    @property
    def database(self):
        return Global.database

    @property
    def creator(self):
        return Global.creator

    @property
    def lock(self):
        return Global.lock

    @property
    def imager(self):
        return Global.imager

    @property
    def executor(self):
        return Global.executor

    @property
    def renderer(self):
        return Global.renderer

    @property
    def rewriter(self):
        return Global.rewriter

    @property
    def progresser(self):
        return Global.progresser


logger = Global.logger
