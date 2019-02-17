# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string


class MissingPage(CommandError):
    """
    Exception class indicating that a CMS page with a predefined ``reverse_id`` is missing.
    """


class MissingAppHook(CommandError):
    """
    Exception class indicating that a page misses the application.
    """


class MissingPlugin(CommandError):
    """
    Exception class indicating that a special plugin is missing or misconfigured on a given
    CMS page.
    """


class Command(BaseCommand):
    help = "Commands for Django-SHOP."

    def add_arguments(self, parser):
        parser.add_argument(
            'subcommand',
            help="./manage.py shop [customers|check-pages]",
        )
        parser.add_argument(
            '--delete-expired',
            action='store_true',
            dest='delete_expired',
            help="Delete customers with expired sessions.",
        )
        parser.add_argument(
            '--add-missing',
            action='store_true',
            dest='add_missing',
            default=False,
            help="Use in combination with 'check-pages': Add missing pages.",
        )

    def handle(self, verbosity, subcommand, *args, **options):
        if subcommand == 'customers':
            self.delete_expired = options['delete_expired']
            self.customers()
        elif subcommand == 'check-pages':
            self.add_missing = options['add_missing']
            self.stdout.write("The following CMS pages must be adjusted:")
            for k, msg in enumerate(self.check_pages(), 1):
                self.stdout.write(" {}. {}".format(k, msg))
        elif subcommand == 'review-settings':
            self.stdout.write("The following configuration settings must be fixed:")
            for k, msg in enumerate(self.review_settings(), 1):
                self.stdout.write(" {}. {}".format(k, msg))
        else:
            msg = "Unknown sub-command for shop. Use one of: customer check-pages review-settings"
            self.stderr.write(msg.format(subcommand))

    def customers(self):
        """
        Entry point for subcommand ``./manage.py shop customers``.
        """
        from shop.models.customer import CustomerModel

        data = dict(total=0, anonymous=0, active=0, staff=0, guests=0, registered=0, expired=0)
        for customer in CustomerModel.objects.iterator():
            data['total'] += 1
            if customer.user.is_active:
                data['active'] += 1
            if customer.user.is_staff:
                data['staff'] += 1
            if customer.is_registered:
                data['registered'] += 1
            elif customer.is_guest:
                data['guests'] += 1
            elif customer.is_anonymous:
                data['anonymous'] += 1
            if customer.is_expired:
                data['expired'] += 1
                if self.delete_expired:
                    customer.delete()
        msg = "Customers in this shop: total={total}, anonymous={anonymous}, expired={expired}, active={active}, guests={guests}, registered={registered}, staff={staff}."
        self.stdout.write(msg.format(**data))

    def check_pages(self):
        """
        Entry point for subcommand ``./manage.py shop check-pages``.
        """
        from cms.models.pagemodel import Page
        from cms.models.pluginmodel import CMSPlugin
        from cms.utils.i18n import get_public_languages

        apphook = self.get_installed_apphook('CatalogListCMSApp')
        catalog_pages = Page.objects.public().filter(application_urls=apphook.__class__.__name__)
        if not catalog_pages.exists():
            if self.add_missing:
                leaf_plugin = self.create_page_structure("Catalog", '', apphook.__class__.__name__)
                self.add_plugin(leaf_plugin, 'ShopCatalogPlugin', {})
                self.publish_in_all_languages(leaf_plugin.page)
                self.assign_all_products_to_page(leaf_plugin.page)
                yield "Created CMS page titled 'Catalog'"
            else:
                yield "There should be at least one published CMS page configured to use an Application inheriting from 'CatalogListCMSApp'."

        page_attributes = [
            ("Search", 'shop-search-product', 'CatalogSearchCMSApp', 'ShopSearchResultsPlugin', {}),
            ("Cart", 'shop-cart', None, 'ShopCartPlugin', {'render_type': 'editable'}),
            ("Watch-List", 'shop-watch-list', None, 'ShopCartPlugin', {'render_type': 'watch'}),
            ("Your Orders", 'shop-order', 'OrderApp', 'ShopOrderViewsPlugin', {}),
            ("Login", 'shop-login', None, 'ShopAuthenticationPlugin', {'form_type': 'login'}),
            ("Register Customer", 'shop-register-customer', None, 'ShopAuthenticationPlugin', {'form_type': 'register-user'}),
            ("Your Personal Details", 'shop-customer-details', None, 'CustomerFormPlugin', {}),
            ("Change Password", 'shop-password-change', None, 'ShopAuthenticationPlugin', {'form_type': 'password-change'}),
            ("Request Password Reset", 'password-reset-request', None, 'ShopAuthenticationPlugin', {'form_type': 'password-reset-request'}),
            ("Confirm Password Reset", 'password-reset-confirm', 'PasswordResetApp', 'ShopAuthenticationPlugin', {'form_type': 'password-reset-confirm'}),
        ]
        for attribs in page_attributes:
            try:
                self.check_page_content(*attribs[1:])
            except MissingPage as exc:
                if self.add_missing:
                    leaf_plugin = self.create_page_structure(*attribs[:3])
                    self.add_plugin(leaf_plugin, *attribs[3:])
                    self.publish_in_all_languages(leaf_plugin.page)
                    yield "Created CMS page titled '{0}'".format(*attribs)
                else:
                    yield str(exc)
            except CommandError as exc:
                yield str(exc)

        # the checkout page must be found through the purchase button
        language = get_public_languages()[0]
        for plugin in CMSPlugin.objects.filter(plugin_type='ShopProceedButton', language=language, placeholder__page__publisher_is_draft=False):
            link = plugin.get_bound_plugin().glossary.get('link')
            if isinstance(link, dict) and link.get('type') == 'PURCHASE_NOW':
                break
        else:
            if self.add_missing:
                column_plugin = self.create_page_structure("Checkout", '', None)
                forms_plugin = self.add_plugin(column_plugin, 'ValidateSetOfFormsPlugin', {})
                glossary = {'button_type': 'btn-success', 'link': {'type': 'PURCHASE_NOW'}, 'link_content': "Purchase Now"}
                self.add_plugin(forms_plugin, 'ShopProceedButton', glossary)
                self.publish_in_all_languages(forms_plugin.page)
                yield "Created CMS page titled 'Checkout'"
            else:
                yield "There should be at least one published CMS page containing a 'Proceed Button Plugin' for purchasing the cart's content."

    def get_installed_apphook(self, base_apphook_name):
        from cms.apphook_pool import apphook_pool
        base_apphook = import_string('shop.cms_apphooks.' + base_apphook_name)

        for apphook, _ in apphook_pool.get_apphooks():
            apphook = apphook_pool.get_apphook(apphook)
            if isinstance(apphook, base_apphook):
                return apphook
        else:
            msg = "The project must register an AppHook inheriting from '{apphook_name}'"
            raise MissingAppHook(msg.format(apphook_name=base_apphook_name))

    def check_page_content(self, reverse_id, base_apphook_name, plugin_type, subset):
        from cms.apphook_pool import apphook_pool
        from cms.models.pagemodel import Page
        from cms.plugin_pool import plugin_pool

        page = Page.objects.public().filter(reverse_id=reverse_id).first()
        if not page:
            msg = "There should be a published CMS page with a reference ID: '{reverse_id}'."
            raise MissingPage(msg.format(reverse_id=reverse_id))

        if base_apphook_name:
            apphook = self.get_installed_apphook(base_apphook_name)
            if apphook_pool.get_apphook(page.application_urls) is not apphook:
                msg = "Page on URL '{url}' must be configured to use Application inheriting from '{app_hook}'."
                raise MissingAppHook(msg.format(url=page.get_absolute_url(), apphook_name=base_apphook_name))

        placeholder = page.placeholders.filter(slot='Main Content').first()
        if not placeholder:
            msg = "Page on URL '{url}' does not contain any plugin."
            raise MissingPlugin(msg.format(url=page.get_absolute_url()))

        plugin_name = plugin_pool.get_plugin(plugin_type).name
        for language in page.get_languages():
            plugin = placeholder.cmsplugin_set.filter(plugin_type=plugin_type, language=language).first()
            if not plugin:
                msg = "Page on URL '{url}' shall contain a plugin named '{plugin_name}'."
                raise MissingPlugin(msg.format(url=page.get_absolute_url(), plugin_name=plugin_name))

            glossary_items = plugin.get_bound_plugin().glossary.items()
            if not all(item in glossary_items for item in subset.items()):
                msg = "Plugin named '{plugin_name}' on page with URL '{url}' is misconfigured."
                raise MissingPlugin(msg.format(url=page.get_absolute_url(), plugin_name=plugin_name))

    def create_page_structure(self, title, reverse_id, base_apphook_name):
        from cms.api import create_page, add_plugin
        from cms.utils.i18n import get_public_languages

        template = settings.CMS_TEMPLATES[0][0]
        apphook = self.get_installed_apphook(base_apphook_name) if base_apphook_name else None
        language = get_public_languages()[0]
        page = create_page(title, template, language, apphook,
                           created_by="manage.py shop check-pages",
                           reverse_id=reverse_id)
        placeholder = page.placeholders.get(slot='Main Content')
        glossary = {
            'breakpoints': ['xs', 'sm', 'md', 'lg', 'xl'],
            'fluid': None,
        }
        container = add_plugin(placeholder, 'BootstrapContainerPlugin', language, glossary=glossary)
        row = add_plugin(placeholder, 'BootstrapRowPlugin', language, target=container)
        glossary = {
            'xs-column-width': 'col',
        }
        return add_plugin(placeholder, 'BootstrapColumnPlugin', language, target=row, glossary=glossary)

    def add_plugin(self, leaf_plugin, plugin_type, glossary):
        from cms.api import add_plugin

        return add_plugin(leaf_plugin.placeholder, plugin_type, leaf_plugin.language, target=leaf_plugin, glossary=glossary)

    def publish_in_all_languages(self, page):
        from cms.api import copy_plugins_to_language, create_title
        from cms.utils.i18n import get_public_languages

        languages = get_public_languages()
        for language in languages[1:]:
            create_title(language, page.get_title(), page, menu_title=None)
            copy_plugins_to_language(page, languages[0], language)
        for language in languages:
            page.publish(language)

    def assign_all_products_to_page(self, page):
        from shop.models.product import ProductModel
        from shop.models.related import ProductPageModel

        for product in ProductModel.objects.all():
            ProductPageModel.objects.create(page=page, product=product)

    def review_settings(self):
        from django.conf import settings

        if getattr(settings, 'AUTH_USER_MODEL', None) != 'email_auth.User':
            yield "settings.AUTH_USER_MODEL should be 'email_auth.User'."

        if 'sass_processor.finders.CssFinder' not in getattr(settings, 'STATICFILES_FINDERS', []):
            yield "settings.STATICFILES_FINDERS should contain 'sass_processor.finders.CssFinder'."

        if 'node_modules' not in dict(getattr(settings, 'STATICFILES_DIRS', [])).keys():
            yield "settings.STATICFILES_DIRS should contain ('node_modules', '/…/node_modules')."

        if '/node_modules/' not in getattr(settings, 'NODE_MODULES_URL', ''):
            yield "settings.NODE_MODULES_URL should start with a URL pointing onto /…/node_modules/."

        for template_engine in getattr(settings, 'TEMPLATES', []):
            if template_engine['BACKEND'] != 'django.template.backends.django.DjangoTemplates':
                continue
            context_processors = template_engine['OPTIONS'].get('context_processors', [])
            if 'shop.context_processors.customer' not in context_processors:
                yield "'shop.context_processors.customer' is missing in 'context_processors' of the default Django Template engine."
            if 'shop.context_processors.shop_settings' not in context_processors:
                yield "'shop.context_processors.shop_settings' is missing in 'context_processors' of the default Django Template engine."

        for dir in getattr(settings, 'SASS_PROCESSOR_INCLUDE_DIRS', []):
            if '/node_modules' in dir:
                break
        else:
            yield "settings.SASS_PROCESSOR_INCLUDE_DIRS should include the folder '…/node_modules'."

        if getattr(settings, 'COERCE_DECIMAL_TO_STRING', None) is not True:
            yield "settings.COERCE_DECIMAL_TO_STRING should be set to 'True'."

        if getattr(settings, 'FSM_ADMIN_FORCE_PERMIT', None) is not True:
            yield "settings.FSM_ADMIN_FORCE_PERMIT should be set to 'True'."

        if getattr(settings, 'SERIALIZATION_MODULES', {}).get('json') != 'shop.money.serializers':
            yield "settings.SERIALIZATION_MODULES['json'] should be set to 'shop.money.serializers'."

        if 'shop.rest.money.JSONRenderer' not in getattr(settings, 'REST_FRAMEWORK', {}).get('DEFAULT_RENDERER_CLASSES', []):
            yield "settings.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] should contain class 'shop.rest.money.JSONRenderer'."

        if 'django_filters.rest_framework.DjangoFilterBackend' not in getattr(settings, 'REST_FRAMEWORK', {}).get('DEFAULT_FILTER_BACKENDS', []):
            yield "settings.REST_FRAMEWORK['DEFAULT_FILTER_BACKENDS'] should contain class 'django_filters.rest_framework.DjangoFilterBackend'."

        if getattr(settings, 'REST_AUTH_SERIALIZERS', {}).get('LOGIN_SERIALIZER') != 'shop.serializers.auth.LoginSerializer':
            yield "settings.REST_AUTH_SERIALIZERS['LOGIN_SERIALIZER'] should be set to 'shop.serializers.auth.LoginSerializer'."

        if 'shop.cascade' not in getattr(settings, 'CMSPLUGIN_CASCADE_PLUGINS', []):
            yield "settings.CMSPLUGIN_CASCADE_PLUGINS should contain entry 'shop.cascade'."