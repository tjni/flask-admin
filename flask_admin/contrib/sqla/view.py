import inspect
import logging
import typing as t
import warnings
from typing import cast as t_cast

from flask import current_app
from flask import flash
from sqlalchemy import Boolean
from sqlalchemy import Column
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import Table
from sqlalchemy import Unicode
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.base import instance_state
from sqlalchemy.orm.base import manager_of_class
from sqlalchemy.sql.expression import cast as sql_cast
from sqlalchemy.sql.expression import desc
from wtforms import Form

from flask_admin._backwards import ObsoleteAttr
from flask_admin._compat import string_types
from flask_admin._compat import text_type
from flask_admin.actions import action
from flask_admin.babel import gettext
from flask_admin.babel import lazy_gettext
from flask_admin.babel import ngettext
from flask_admin.contrib.sqla import filters as sqla_filters
from flask_admin.contrib.sqla import form
from flask_admin.contrib.sqla import tools
from flask_admin.contrib.sqla.tools import is_relationship
from flask_admin.model import BaseModelView
from flask_admin.model.form import create_editable_list_form

from ..._types import T_COLUMN
from ..._types import T_FIELD_ARGS_VALIDATORS
from ..._types import T_FILTER
from ..._types import T_SQLALCHEMY_INLINE_MODELS
from ..._types import T_SQLALCHEMY_MODEL
from ..._types import T_SQLALCHEMY_QUERY
from ..._types import T_SQLALCHEMY_SESSION
from ..._types import T_WIDGET
from .ajax import create_ajax_loader
from .ajax import QueryAjaxModelLoader
from .filters import BaseSQLAFilter
from .typefmt import DEFAULT_FORMATTERS

# Set up logger
log = logging.getLogger("flask-admin.sqla")


class ModelView(BaseModelView):
    """
    SQLAlchemy model view

    Usage sample::

        admin = Admin()
        admin.add_view(ModelView(User, db.session))
    """

    column_auto_select_related: bool = t_cast(
        bool, ObsoleteAttr("column_auto_select_related", "auto_select_related", True)
    )
    """
        Enable automatic detection of displayed foreign keys in this view
        and perform automatic joined loading for related models to improve
        query performance.

        Please note that detection is not recursive: if `__unicode__` method
        of related model uses another model to generate string representation, it
        will still make separate database call.
    """

    column_select_related_list: t.Optional[t.Sequence[str]] = t_cast(
        t.Optional[t.Sequence[str]],
        ObsoleteAttr("column_select_related", "list_select_related", None),
    )
    """
        List of parameters for SQLAlchemy `subqueryload`. Overrides
        `column_auto_select_related` property.

        For example::

            class PostAdmin(ModelView):
                column_select_related_list = ('user', 'city')

        You can also use properties::

            class PostAdmin(ModelView):
                column_select_related_list = (Post.user, Post.city)

        Please refer to the `subqueryload` on list of possible values.
    """

    column_display_all_relations: t.Optional[bool] = t_cast(
        bool,
        ObsoleteAttr(
            "column_display_all_relations", "list_display_all_relations", False
        ),
    )
    """
        Controls if list view should display all relations, not only many-to-one.
    """

    column_searchable_list = t_cast(
        t.Optional[t.Sequence[str]],
        ObsoleteAttr("column_searchable_list", "searchable_columns", None),
    )
    """
        Collection of the searchable columns.

        Example::

            class MyModelView(ModelView):
                column_searchable_list = ('name', 'email')

        You can also pass columns::

            class MyModelView(ModelView):
                column_searchable_list = (User.name, User.email)

        The following search rules apply:

        - If you enter ``ZZZ`` in the UI search field, it will generate
          ``ILIKE '%ZZZ%'`` statement against searchable columns.

        - If you enter multiple words, each word will be searched separately, but
          only rows that contain all words will be displayed. For example, searching
          for ``abc def`` will find all rows that contain ``abc`` and ``def`` in one or
          more columns.

        - If you prefix your search term with ``^``, it will find all rows
          that start with ``^``. So, if you entered ``^ZZZ`` then ``ILIKE 'ZZZ%'`` will
          be used.

        - If you prefix your search term with ``=``, it will perform an exact match.
          For example, if you entered ``=ZZZ``, the statement ``ILIKE 'ZZZ'`` will be
          used.
    """

    column_filters: t.Optional[t.Sequence[str]] = None
    """
        Collection of the column filters.

        Can contain either field names or instances of
        :class:`flask_admin.contrib.sqla.filters.BaseSQLAFilter` classes.

        Filters will be grouped by name when displayed in the drop-down.

        For example::

            class MyModelView(BaseModelView):
                column_filters = ('user', 'email')

        or::

            from flask_admin.contrib.sqla.filters import BooleanEqualFilter

            class MyModelView(BaseModelView):
                column_filters = (BooleanEqualFilter(column=User.name, name='Name'),)

        or::

            from flask_admin.contrib.sqla.filters import BaseSQLAFilter

            class FilterLastNameBrown(BaseSQLAFilter):
                def apply(self, query, value, alias=None):
                    if value == '1':
                        return query.filter(self.column == "Brown")
                    else:
                        return query.filter(self.column != "Brown")

                def operation(self):
                    return 'is Brown'

            class MyModelView(BaseModelView):
                column_filters = [
                    FilterLastNameBrown(
                        User.last_name, 'Last Name', options=(('1', 'Yes'), ('0', 'No'))
                    )
                ]
    """

    model_form_converter: type[form.AdminModelConverter] = form.AdminModelConverter
    """
        Model form conversion class. Use this to implement custom field conversion
        logic.

        For example::

            class MyModelConverter(AdminModelConverter):
                pass


            class MyAdminView(ModelView):
                model_form_converter = MyModelConverter
    """

    inline_model_form_converter: type[form.InlineModelConverter] = (
        form.InlineModelConverter
    )
    """
        Inline model conversion class. If you need some kind of post-processing for
        inline forms, you can customize behavior by doing something like this::

            class MyInlineModelConverter(InlineModelConverter):
                def post_process(self, form_class, info):
                    form_class.value = wtf.StringField('value')
                    return form_class

            class MyAdminView(ModelView):
                inline_model_form_converter = MyInlineModelConverter
    """

    filter_converter: sqla_filters.FilterConverter = sqla_filters.FilterConverter()
    """
        Field to filter converter.

        Override this attribute to use non-default converter.
    """

    fast_mass_delete: bool = False
    """
        If set to `False` and user deletes more than one model using built in action,
        all models will be read from the database and then deleted one by one
        giving SQLAlchemy a chance to manually cleanup any dependencies (many-to-many
        relationships, etc).

        If set to `True`, will run a ``DELETE`` statement which is somewhat faster,
        but may leave corrupted data if you forget to configure ``DELETE
        CASCADE`` for your model.
    """

    inline_models: t.Optional[T_SQLALCHEMY_INLINE_MODELS] = None
    """
        Inline related-model editing for models with parent-child relations.

        Accepts enumerable with one of the following possible values:

        1. Child model class::

            class MyModelView(ModelView):
                inline_models = (Post,)

        2. Child model class and additional options::

            class MyModelView(ModelView):
                inline_models = [(Post, dict(form_columns=['title']))]

        3. Django-like ``InlineFormAdmin`` class instance::

            from flask_admin.model.form import InlineFormAdmin

            class MyInlineModelForm(InlineFormAdmin):
                form_columns = ('title', 'date')

            class MyModelView(ModelView):
                inline_models = (MyInlineModelForm(MyInlineModel),)

        You can customize the generated field name by:

        1. Using the `form_name` property as a key to the options dictionary::

            class MyModelView(ModelView):
                inline_models = ((Post, dict(form_label='Hello')))

        2. Using forward relation name and `column_labels` property::

            class Model1(Base):
                pass

            class Model2(Base):
                # ...
                model1 = relation(Model1, backref='models')

            class MyModel1View(Base):
                inline_models = (Model2,)
                column_labels = {'models': 'Hello'}

        By default used ManyToMany relationship for inline models.
        You may configure inline model for OneToOne relationship.
        To achieve this, you need to install special ``inline_converter``
        for your model::

            from flask_admin.contrib.sqla.form import \
                InlineOneToOneModelConverter

            class MyInlineModelForm(InlineFormAdmin):
                form_columns = ('title', 'date')
                inline_converter = InlineOneToOneModelConverter

            class MyModelView(ModelView):
                inline_models = (MyInlineModelForm(MyInlineModel),)
    """

    column_type_formatters: dict[type, t.Callable[[BaseModelView, t.Any, str], str]] = (
        DEFAULT_FORMATTERS
    )

    form_choices: t.Optional[dict[str, list[tuple[str, str]]]] = None
    """
        Map choices to form fields

        Example::

            class MyModelView(BaseModelView):
                form_choices = {'my_form_field': [
                    ('db_value', 'display_value'),
                ]}
    """

    form_optional_types: t.Sequence[type] = (Boolean,)
    """
        List of field types that should be optional if column is not nullable.

        Example::

            class MyModelView(BaseModelView):
                form_optional_types = (Boolean, Unicode)
    """

    ignore_hidden: bool = True
    """
       Ignore field that starts with "_"

       Example::

           class MyModelView(BaseModelView):
               ignore_hidden = False
    """

    def __init__(
        self,
        model: type[T_SQLALCHEMY_MODEL],
        session: T_SQLALCHEMY_SESSION,
        name: t.Optional[str] = None,
        category: t.Optional[str] = None,
        endpoint: t.Optional[str] = None,
        url: t.Optional[str] = None,
        static_folder: t.Optional[str] = None,
        menu_class_name: t.Optional[str] = None,
        menu_icon_type: t.Optional[str] = None,
        menu_icon_value: t.Optional[str] = None,
    ) -> None:
        """
        Constructor.

        :param model:
            Model class
        :param session:
            SQLAlchemy session
        :param name:
            View name. If not set, defaults to the model name
        :param category:
            Category name
        :param endpoint:
            Endpoint name. If not set, defaults to the model name
        :param url:
            Base URL. If not set, defaults to '/admin/' + endpoint
        :param menu_class_name:
            Optional class name for the menu item.
        :param menu_icon_type:
            Optional icon. Possible icon types:

             - `flask_admin.consts.ICON_TYPE_GLYPH` - Bootstrap glyph icon
             - `flask_admin.consts.ICON_TYPE_FONT_AWESOME` - Font Awesome icon
             - `flask_admin.consts.ICON_TYPE_IMAGE` - Image relative to Flask static
                directory
             - `flask_admin.consts.ICON_TYPE_IMAGE_URL` - Image with full URL
        :param menu_icon_value:
            Icon glyph name or URL, depending on `menu_icon_type` setting
        """
        self.session = session

        self._search_fields: t.Optional[list[tuple[Column, t.Any]]] = None

        self._filter_joins: dict = dict()

        self._sortable_joins: dict = dict()

        if self.form_choices is None:
            self.form_choices = {}

        super().__init__(
            model,
            name,
            category,
            endpoint,
            url,
            static_folder,
            menu_class_name=menu_class_name,
            menu_icon_type=menu_icon_type,
            menu_icon_value=menu_icon_value,
        )
        self.model = t.cast(type[T_SQLALCHEMY_MODEL], self.model)
        self._manager = manager_of_class(self.model)

        # Primary key
        self._primary_key = self.scaffold_pk()

        if self._primary_key is None:
            raise Exception(f"Model {self.model.__name__} does not have primary key.")

        # Configuration
        self._auto_joins: t.Iterable
        if not self.column_select_related_list:
            self._auto_joins = self.scaffold_auto_joins()
        else:
            self._auto_joins = self.column_select_related_list

    # Internal API
    def _get_model_iterator(
        self, model: t.Optional[type[T_SQLALCHEMY_MODEL]] = None
    ) -> t.Iterable:
        """
        Return property iterator for the model
        """
        if model is None:
            model = self.model  # type: ignore[assignment]

        return model._sa_class_manager.mapper.attrs  # type: ignore[union-attr]

    def _apply_path_joins(
        self,
        query: T_SQLALCHEMY_QUERY,
        joins: dict,
        path: t.Optional[t.Iterable],
        inner_join: bool = True,
    ) -> tuple[T_SQLALCHEMY_QUERY, dict, t.Optional[t.Any]]:
        """
        Apply join path to the query.

        :param query:
            Query to add joins to
        :param joins:
            List of current joins. Used to avoid joining on same relationship more
            than once
        :param path:
            Path to be joined
        :param fn:
            Join function
        """
        last = None

        if path:
            for item in path:
                key = (inner_join, item)
                alias = joins.get(key)

                if key not in joins:
                    if not isinstance(item, Table):
                        alias = aliased(item.property.mapper.class_)

                    fn = query.join if inner_join else query.outerjoin

                    if last is None:
                        query = fn(item) if alias is None else fn(alias, item)
                    else:
                        prop = getattr(last, item.key)
                        query = fn(prop) if alias is None else fn(alias, prop)

                    joins[key] = alias

                last = alias

        return query, joins, last

    # Scaffolding
    def scaffold_pk(self) -> t.Union[t.Any, tuple[t.Any, ...]]:
        """
        Return the primary key name(s) from a model
        If model has single primary key, will return a string and tuple otherwise
        """
        return tools.get_primary_key(self.model)

    def get_pk_value(
        self,
        model: type[T_SQLALCHEMY_MODEL],  # type: ignore[override]
    ) -> t.Union[t.Any, tuple[str, ...]]:
        """
        Return the primary key value from a model object.
        If there are multiple primary keys, they're encoded into string representation.
        """
        if isinstance(self._primary_key, tuple):
            return tools.iterencode(getattr(model, attr) for attr in self._primary_key)
        else:
            return tools.escape(getattr(model, self._primary_key))

    def scaffold_list_columns(self) -> list:
        """
        Return a list of columns from the model.
        """
        columns = []

        for p in self._get_model_iterator():
            if hasattr(p, "direction"):
                if self.column_display_all_relations or p.direction.name == "MANYTOONE":
                    columns.append(p.key)
            elif hasattr(p, "columns"):
                if len(p.columns) > 1:
                    filtered = tools.filter_foreign_columns(
                        self.model.__table__,  # type: ignore[union-attr]
                        p.columns,
                    )

                    if len(filtered) == 0:
                        continue
                    elif len(filtered) > 1:
                        warnings.warn(
                            (
                                f"Can not convert multiple-column "
                                f"properties ({self.model}.{p.key})"
                            ),
                            stacklevel=1,
                        )
                        continue

                    column = filtered[0]
                else:
                    column = p.columns[0]

                if column.foreign_keys:
                    continue

                if not self.column_display_pk and column.primary_key:
                    continue

                columns.append(p.key)

        return columns

    def scaffold_sortable_columns(self) -> dict[T_COLUMN, T_COLUMN]:
        """
        Return a dictionary of sortable columns.
        Key is column name, value is sort column/field.
        """
        columns = dict()

        for p in self._get_model_iterator():
            if hasattr(p, "columns"):
                # Sanity check
                if len(p.columns) > 1:
                    # Multi-column properties are not supported
                    continue

                column = p.columns[0]

                # Can't sort on primary or foreign keys by default
                if column.foreign_keys:
                    continue

                if not self.column_display_pk and column.primary_key:
                    continue

                columns[p.key] = column

        return columns

    def get_sortable_columns(self) -> dict[T_COLUMN, T_COLUMN]:
        """
        Returns a dictionary of the sortable columns. Key is a model
        field name and value is sort column (for example - attribute).

        If `column_sortable_list` is set, will use it. Otherwise, will call
        `scaffold_sortable_columns` to get them from the model.
        """
        self._sortable_joins = dict()

        if self.column_sortable_list is None:
            return self.scaffold_sortable_columns()
        else:
            result = dict()

            for c in self.column_sortable_list:
                if isinstance(c, tuple):
                    if isinstance(c[1], tuple):
                        column, path = [], []
                        for item in c[1]:
                            column_item, path_item = tools.get_field_with_path(
                                self.model, item
                            )
                            column.append(column_item)
                            path.append(path_item)
                        column_name = c[0]
                    else:
                        column, path = tools.get_field_with_path(self.model, c[1])
                        column_name = c[0]
                else:
                    column, path = tools.get_field_with_path(
                        self.model,  # type: ignore[arg-type]
                        c,  # type: ignore[arg-type]
                    )
                    column_name = text_type(c)

                if path and (hasattr(path[0], "property") or isinstance(path[0], list)):
                    self._sortable_joins[column_name] = path
                elif path:
                    raise Exception(
                        "For sorting columns in a related table, "
                        "column_sortable_list requires a string "
                        "like '<relation name>.<column name>'. "
                        f"Failed on: {c}"
                    )
                else:
                    # column is in same table, use only model attribute name
                    if getattr(column, "key", None) is not None:
                        column_name = column.key  # type: ignore[union-attr]

                # column_name must match column_name used in `get_list_columns`
                result[column_name] = column

            return result  # type: ignore[return-value]

    def get_column_names(
        self,
        only_columns: t.Iterable[T_COLUMN],
        excluded_columns: t.Optional[t.Iterable[T_COLUMN]],
    ) -> list[tuple[T_COLUMN, str]]:
        """
        Returns a list of tuples with the model field name and formatted
        field name.

        Overridden to handle special columns like InstrumentedAttribute.

        :param only_columns:
            List of columns to include in the results. If not set,
            `scaffold_list_columns` will generate the list from the model.
        :param excluded_columns:
            List of columns to exclude from the results.
        """
        if excluded_columns:
            only_columns = [c for c in only_columns if c not in excluded_columns]

        formatted_columns: list[tuple[T_COLUMN, str]] = []
        for c in only_columns:
            try:
                column, path = tools.get_field_with_path(
                    self.model,  # type: ignore[arg-type]
                    c,  # type: ignore[arg-type]
                )

                if path:
                    # column is a relation (InstrumentedAttribute), use full path
                    column_name = text_type(c)
                else:
                    # column is in same table, use only model attribute name
                    if getattr(column, "key", None) is not None:
                        column_name = column.key  # type: ignore[union-attr]
                    else:
                        column_name = text_type(c)
            except AttributeError:
                # TODO: See ticket #1299 - allow virtual columns. Probably figure out
                # better way to handle it. For now just assume if column was not found
                # - it is virtual and there's column formatter for it.
                column_name = text_type(c)

            visible_name = self.get_column_name(column_name)

            # column_name must match column_name in `get_sortable_columns`
            formatted_columns.append((column_name, visible_name))

        return formatted_columns

    def init_search(self) -> bool:
        """
        Initialize search. Returns `True` if search is supported for this
        view.

        For SQLAlchemy, this will initialize internal fields: list of
        column objects used for filtering, etc.
        """
        if self.column_searchable_list:
            self._search_fields = []

            for name in self.column_searchable_list:
                attr, joins = tools.get_field_with_path(
                    self.model,  # type: ignore[arg-type]
                    name,
                )

                if not attr:
                    raise Exception(f"Failed to find field for search field: {name}")

                if tools.is_hybrid_property(self.model, name):  # type: ignore[arg-type]
                    column = attr
                    if isinstance(name, string_types):
                        column.key = name.split(".")[-1]
                    self._search_fields.append((column, joins))
                else:
                    for column in tools.get_columns_for_field(attr):
                        self._search_fields.append((column, joins))

        return bool(self.column_searchable_list)

    def search_placeholder(self) -> t.Optional[str]:
        """
        Return search placeholder.

        For example, if set column_labels and column_searchable_list:

        class MyModelView(BaseModelView):
            column_labels = dict(name='Name', last_name='Last Name')
            column_searchable_list = ('name', 'last_name')

        placeholder is: "Name, Last Name"
        """
        if not self.column_searchable_list:
            return None

        placeholders = []

        for searchable in self.column_searchable_list:
            if isinstance(searchable, InstrumentedAttribute):
                placeholders.append(
                    str(self.column_labels.get(searchable.key, searchable.key))
                )
            else:
                placeholders.append(str(self.column_labels.get(searchable, searchable)))

        return ", ".join(placeholders)

    def scaffold_filters(  # type: ignore[override]
        self, name: t.Any
    ) -> t.Optional[list[BaseSQLAFilter]]:
        """
        Return list of enabled filters
        """

        attr, joins = tools.get_field_with_path(self.model, name)  # type: ignore[arg-type]

        if attr is None:
            raise Exception(f"Failed to find field for filter: {name}")

        # Figure out filters for related column
        if is_relationship(attr):
            filters = []

            for p in self._get_model_iterator(attr.property.mapper.class_):
                if hasattr(p, "columns"):
                    # TODO: Check for multiple columns
                    column = p.columns[0]

                    if column.foreign_keys or column.primary_key:
                        continue

                    visible_name = (
                        f"{self.get_column_name(attr.prop.target.name)}"
                        f" / {self.get_column_name(p.key)}"
                    )

                    type_name = type(column.type).__name__
                    flt = self.filter_converter.convert(type_name, column, visible_name)

                    if flt:
                        table = column.table

                        if joins:
                            self._filter_joins[column] = joins
                        elif tools.need_join(self.model, table):  # type: ignore[arg-type]
                            self._filter_joins[column] = [table]

                        filters.extend(flt)

            return filters
        else:
            is_hybrid_property = tools.is_hybrid_property(
                self.model,  # type: ignore[arg-type]
                name,
            )
            if is_hybrid_property:
                column = attr
                if isinstance(name, string_types):
                    column.key = name.split(".")[-1]
            else:
                columns = tools.get_columns_for_field(attr)

                if len(columns) > 1:
                    raise Exception(
                        f"Can not filter more than on one column for {name}"
                    )

                column = columns[0]

            # If filter related to relation column (represented by
            # relation_name.target_column) we collect here relation name
            joined_column_name = None
            if isinstance(name, string_types) and "." in name:
                joined_column_name = name.split(".")[0]

            # Join not needed for hybrid properties
            if (
                not is_hybrid_property
                and tools.need_join(self.model, column.table)  # type: ignore[arg-type]
                and name not in self.column_labels
            ):
                if joined_column_name:
                    visible_name = (
                        f"{joined_column_name}"
                        f" / {self.get_column_name(column.table.name)}"
                        f" / {self.get_column_name(column.name)}"
                    )
                else:
                    visible_name = (
                        f"{self.get_column_name(column.table.name)}"
                        f" / {self.get_column_name(column.name)}"
                    )
            else:
                if not isinstance(name, string_types):
                    visible_name = self.get_column_name(name.property.key)
                else:
                    if self.column_labels and name in self.column_labels:
                        visible_name = self.column_labels[name]
                    else:
                        visible_name = self.get_column_name(name)
                        visible_name = visible_name.replace(".", " / ")

            type_name = type(column.type).__name__

            flt = self.filter_converter.convert(
                type_name,
                column,
                visible_name,
                options=self.column_choices.get(name),  # type: ignore[union-attr]
            )

            key_name = column
            # In case of filter related to relation column filter key
            # must be named with relation name (to prevent following same
            # target column to replace previous)
            if joined_column_name:
                key_name = f"{joined_column_name}.{column}"
                for f in flt:  # type: ignore[union-attr]
                    f.key_name = key_name

            if joins:
                self._filter_joins[key_name] = joins
            elif not is_hybrid_property and tools.need_join(
                self.model,  # type: ignore[arg-type]
                column.table,
            ):
                self._filter_joins[key_name] = [column.table]

            return flt

    def handle_filter(self, filter: t.Any) -> t.Any:
        if isinstance(filter, sqla_filters.BaseSQLAFilter):
            column = filter.column

            # hybrid_property joins are not supported yet
            if isinstance(column, InstrumentedAttribute) and tools.need_join(
                self.model, column.table
            ):
                self._filter_joins[column] = [column.table]

        return filter

    def scaffold_form(self) -> type[Form]:
        """
        Create form from the model.
        """
        converter = self.model_form_converter(self.session, self)
        form_class = form.get_form(
            self.model,  # type: ignore[arg-type]
            converter,
            base_class=self.form_base_class,
            only=self.form_columns,
            exclude=self.form_excluded_columns,
            field_args=self.form_args,
            ignore_hidden=self.ignore_hidden,
            extra_fields=self.form_extra_fields,  # type: ignore[arg-type]
        )

        if self.inline_models:
            form_class = self.scaffold_inline_form_models(form_class)

        return form_class

    def scaffold_list_form(
        self,
        widget: t.Optional[type[T_WIDGET]] = None,
        validators: t.Optional[dict[str, T_FIELD_ARGS_VALIDATORS]] = None,
    ) -> type[Form]:
        """
        Create form for the `index_view` using only the columns from
        `self.column_editable_list`.

        :param widget:
            WTForms widget class. Defaults to `XEditableWidget`.
        :param validators:
            `form_args` dict with only validators
            {'name': {'validators': [required()]}}
        """
        converter = self.model_form_converter(self.session, self)
        form_class = form.get_form(
            self.model,  # type: ignore[arg-type]
            converter,
            base_class=self.form_base_class,
            only=self.column_editable_list,
            field_args=validators,
        )

        return create_editable_list_form(self.form_base_class, form_class, widget)

    def scaffold_inline_form_models(self, form_class: type[Form]) -> type[Form]:
        """
        Contribute inline models to the form

        :param form_class:
            Form class
        """
        default_converter = self.inline_model_form_converter(
            self.session, self, self.model_form_converter
        )

        for m in self.inline_models:  # type: ignore[union-attr]
            if not hasattr(m, "inline_converter"):
                form_class = default_converter.contribute(
                    self.model,  # type: ignore[arg-type]
                    form_class,
                    m,  # type: ignore[arg-type]
                )
                continue

            custom_converter = m.inline_converter(
                self.session, self, self.model_form_converter
            )
            form_class = custom_converter.contribute(self.model, form_class, m)
        return form_class

    def scaffold_auto_joins(self) -> list:
        """
        Return a list of joined tables by going through the
        displayed columns.
        """
        if not self.column_auto_select_related:
            return []

        relations = set()

        for p in self._get_model_iterator():
            if hasattr(p, "direction"):
                # Check if it is pointing to same model
                if p.mapper.class_ == self.model:
                    continue

                # Check if it is pointing to a differnet bind
                source_bind = getattr(self.model, "__bind_key__", None)
                target_bind = getattr(p.mapper.class_, "__bind_key__", None)
                if source_bind != target_bind:
                    continue

                if p.direction.name in ["MANYTOONE", "MANYTOMANY"]:
                    relations.add(p.key)

        joined = []

        for prop, _name in self._list_columns:
            if prop in relations:
                joined.append(getattr(self.model, prop))  # type: ignore[arg-type]

        return joined

    # AJAX foreignkey support
    def _create_ajax_loader(
        self, name: str, options: dict[str, t.Any]
    ) -> QueryAjaxModelLoader:
        return create_ajax_loader(self.model, self.session, name, name, options)

    # Database-related API
    def get_query(self) -> T_SQLALCHEMY_QUERY:
        """
        Return a query for the model type.

        This method can be used to set a "persistent filter" on an index_view.

        Example::

            class MyView(ModelView):
                def get_query(self):
                    return super(MyView, self).get_query().filter(
                        User.username == current_user.username
                    )


        If you override this method, don't forget to also override `get_count_query`,
        for displaying the correct item count in the list view, and `get_one`, which is
        used when retrieving records for the edit view.
        """
        return self.session.query(self.model)

    def get_count_query(self) -> T_SQLALCHEMY_QUERY:
        """
        Return a the count query for the model type

        A ``query(self.model).count()`` approach produces an excessive
        subquery, so ``query(func.count('*'))`` should be used instead.

        See commit ``#45a2723`` for details.
        """
        return self.session.query(func.count("*")).select_from(self.model)

    def _order_by(
        self,
        query: T_SQLALCHEMY_QUERY,
        joins: dict,
        sort_joins: dict,
        sort_field: t.Optional[InstrumentedAttribute],
        sort_desc: bool,
    ) -> tuple[T_SQLALCHEMY_QUERY, dict]:
        """
        Apply order_by to the query

        :param query:
            Query
        :pram joins:
            Current joins
        :param sort_joins:
            Sort joins (properties or tables)
        :param sort_field:
            Sort field
        :param sort_desc:
            Ascending or descending
        """
        if sort_field is not None:
            # Handle joins
            query, joins, alias = self._apply_path_joins(
                query, joins, sort_joins, inner_join=False
            )

            column = sort_field if alias is None else getattr(alias, sort_field.key)

            if sort_desc:
                query = query.order_by(desc(column))
            else:
                query = query.order_by(column)

        return query, joins

    def _get_default_order(  # type: ignore[override]
        self,
    ) -> t.Generator[tuple[t.Optional[t.Any], list, bool], None, None]:
        order = super()._get_default_order()
        for field, direction in order or []:
            attr, joins = tools.get_field_with_path(
                self.model,  # type: ignore[arg-type]
                field,
            )
            yield attr, joins, direction

    def _apply_sorting(
        self,
        query: T_SQLALCHEMY_QUERY,
        joins: dict,
        sort_column: t.Optional[T_COLUMN],
        sort_desc: bool,
    ) -> tuple[T_SQLALCHEMY_QUERY, dict]:
        if sort_column is not None:
            if sort_column in self._sortable_columns:
                sort_field = t.cast(
                    InstrumentedAttribute, self._sortable_columns[sort_column]
                )
                sort_joins = t.cast(dict, self._sortable_joins.get(sort_column))

                if isinstance(sort_field, list):
                    for field_item, join_item in zip(sort_field, sort_joins):
                        query, joins = self._order_by(
                            query, joins, join_item, field_item, sort_desc
                        )
                else:
                    query, joins = self._order_by(
                        query, joins, sort_joins, sort_field, sort_desc
                    )
        else:
            order = self._get_default_order()
            for sort_field, sort_joins, sort_desc in order:  # type: ignore[assignment]
                query, joins = self._order_by(
                    query, joins, sort_joins, sort_field, sort_desc
                )

        return query, joins

    def _apply_search(
        self,
        query: T_SQLALCHEMY_QUERY,
        count_query: t.Optional[T_SQLALCHEMY_QUERY],
        joins: dict,
        count_joins: dict,
        search: str,
    ) -> tuple[T_SQLALCHEMY_QUERY, t.Optional[T_SQLALCHEMY_QUERY], dict, dict]:
        """
        Apply search to a query.
        """
        terms = search.split(" ")

        for term in terms:
            if not term:
                continue

            stmt = tools.parse_like_term(term)

            filter_stmt = []
            count_filter_stmt: list = []

            for field, path in self._search_fields:  # type: ignore[union-attr]
                query, joins, alias = self._apply_path_joins(
                    query, joins, path, inner_join=False
                )

                count_alias = None

                if count_query is not None:
                    count_query, count_joins, count_alias = self._apply_path_joins(
                        count_query, count_joins, path, inner_join=False
                    )

                column = field if alias is None else getattr(alias, field.key)
                filter_stmt.append(sql_cast(column, Unicode).ilike(stmt))

                if count_filter_stmt is not None:
                    column = (
                        field
                        if count_alias is None
                        else getattr(count_alias, field.key)
                    )
                    count_filter_stmt.append(sql_cast(column, Unicode).ilike(stmt))

            query = query.filter(or_(*filter_stmt))

            if count_query is not None:
                count_query = count_query.filter(or_(*count_filter_stmt))

        return query, count_query, joins, count_joins

    def _apply_filters(
        self,
        query: T_SQLALCHEMY_QUERY,
        count_query: t.Optional[T_SQLALCHEMY_QUERY],
        joins: dict,
        count_joins: dict,
        filters: t.Sequence[T_FILTER],
    ) -> tuple[T_SQLALCHEMY_QUERY, t.Optional[T_SQLALCHEMY_QUERY], dict, dict]:
        for idx, _flt_name, value in filters:
            flt = self._filters[idx]  # type: ignore[index]

            alias = None
            count_alias = None

            # Figure out joins
            if isinstance(flt, sqla_filters.BaseSQLAFilter):
                # If no key_name is specified, use filter column as filter key
                filter_key = flt.key_name or flt.column
                path = self._filter_joins.get(filter_key, [])

                query, joins, alias = self._apply_path_joins(
                    query, joins, path, inner_join=False
                )

                if count_query is not None:
                    count_query, count_joins, count_alias = self._apply_path_joins(
                        count_query, count_joins, path, inner_join=False
                    )

            # Clean value .clean() and apply the filter
            clean_value = flt.clean(value)

            try:
                query = flt.apply(query, clean_value, alias)  # type: ignore[call-arg]
            except TypeError:
                spec = inspect.getfullargspec(flt.apply)

                if len(spec.args) == 3:
                    warnings.warn(
                        f"Please update your custom filter {repr(flt)} to "
                        "include additional `alias` parameter.",
                        stacklevel=1,
                    )
                else:
                    raise

                query = flt.apply(query, clean_value)

            if count_query is not None:
                try:
                    count_query = flt.apply(  # type: ignore[call-arg]
                        count_query, clean_value, count_alias
                    )
                except TypeError:
                    count_query = flt.apply(count_query, clean_value)

        return query, count_query, joins, count_joins

    def _apply_pagination(
        self,
        query: T_SQLALCHEMY_QUERY,
        page: t.Optional[int],
        page_size: t.Optional[int],
    ) -> T_SQLALCHEMY_QUERY:
        if page_size is None:
            page_size = self.page_size

        if page_size:
            query = query.limit(page_size)

        if page and page_size:
            query = query.offset(page * page_size)

        return query

    def get_list(  # type: ignore[override]
        self,
        page: t.Optional[int],
        sort_column: t.Optional[T_COLUMN],
        sort_desc: bool,
        search: t.Optional[str],
        filters: t.Optional[t.Sequence[T_FILTER]],
        execute: bool = True,
        page_size: t.Optional[int] = None,
    ) -> tuple[t.Optional[int], list[T_SQLALCHEMY_MODEL]]:
        """
        Return records from the database.

        :param page:
            Page number
        :param sort_column:
            Sort column name
        :param sort_desc:
            Descending or ascending sort
        :param search:
            Search query
        :param execute:
            Execute query immediately? Default is `True`
        :param filters:
            List of filter tuples
        :param page_size:
            Number of results. Defaults to ModelView's page_size. Can be
            overriden to change the page_size limit. Removing the page_size
            limit requires setting page_size to 0 or False.
        """

        # Will contain join paths with optional aliased object
        joins: dict = {}
        count_joins: dict = {}

        query = self.get_query()
        count_query = self.get_count_query() if not self.simple_list_pager else None

        # Ignore eager-loaded relations (prevent unnecessary joins)
        # TODO: Separate join detection for query and count query?
        if hasattr(query, "_join_entities"):
            for entity in query._join_entities:
                for table in entity.tables:
                    joins[table] = None

        # Apply search criteria
        if self._search_supported and search:
            query, count_query, joins, count_joins = self._apply_search(
                query, count_query, joins, count_joins, search
            )

        # Apply filters
        if filters and self._filters:
            query, count_query, joins, count_joins = self._apply_filters(
                query, count_query, joins, count_joins, filters
            )

        # Calculate number of rows if necessary
        count = count_query.scalar() if count_query else None

        # Auto join
        for j in self._auto_joins:
            query = query.options(joinedload(j))

        # Sorting
        query, joins = self._apply_sorting(query, joins, sort_column, sort_desc)

        # Pagination
        query = self._apply_pagination(query, page, page_size)

        # Execute if needed
        if execute:
            query = query.all()  # type: ignore[assignment, union-attr]

        return count, query  # type: ignore[return-value]

    def get_one(self, id: t.Any) -> t.Any:
        """
        Return a single model by its id.

        Example::

            def get_one(self, id):
                query = self.get_query()
                return query.filter(self.model.id == id).one()

        Also see `get_query` for how to filter the list view.

        :param id:
            Model id
        """
        return self.session.get(self.model, tools.iterdecode(id))

    # Error handler
    def handle_view_exception(self, exc: Exception) -> bool:
        if isinstance(exc, IntegrityError):
            if current_app.config.get(
                "FLASK_ADMIN_RAISE_ON_INTEGRITY_ERROR",
                current_app.config.get("FLASK_ADMIN_RAISE_ON_VIEW_EXCEPTION"),
            ):
                raise
            else:
                flash(
                    gettext("Integrity error. %(message)s", message=text_type(exc)),
                    "error",
                )
            return True

        return super().handle_view_exception(exc)

    def build_new_instance(self) -> T_SQLALCHEMY_MODEL:
        """
        Build new instance of a model. Useful to override the Flask-Admin behavior
        when the model has a custom __init__ method.
        """
        model = self._manager.new_instance()

        # TODO: We need a better way to create model instances and stay compatible with
        # SQLAlchemy __init__() behavior
        state = instance_state(model)
        self._manager.dispatch.init(state, [], {})

        return model

    # Model handlers
    def create_model(self, form: Form) -> t.Union[bool, T_SQLALCHEMY_MODEL]:
        """
        Create model from form.

        :param form:
            Form instance
        """
        try:
            model = self.build_new_instance()

            form.populate_obj(model)
            self.session.add(model)
            self._on_model_change(form, model, True)
            self.session.commit()
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Failed to create record. %(error)s", error=str(ex)),
                    "error",
                )
                log.exception("Failed to create record.")

            self.session.rollback()

            return False
        else:
            self.after_model_change(form, model, True)

        return model

    def update_model(  # type: ignore[override]
        self, form: Form, model: T_SQLALCHEMY_MODEL
    ) -> bool:
        """
        Update model from form.

        :param form:
            Form instance
        :param model:
            Model instance
        """
        try:
            form.populate_obj(model)
            self._on_model_change(form, model, False)
            self.session.commit()
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Failed to update record. %(error)s", error=str(ex)),
                    "error",
                )
                log.exception("Failed to update record.")

            self.session.rollback()

            return False
        else:
            self.after_model_change(form, model, False)

        return True

    def delete_model(self, model: T_SQLALCHEMY_MODEL) -> bool:  # type: ignore[override]
        """
        Delete model.

        :param model:
            Model to delete
        """
        try:
            self.on_model_delete(model)
            self.session.flush()
            self.session.delete(model)
            self.session.commit()
        except Exception as ex:
            if not self.handle_view_exception(ex):
                flash(
                    gettext("Failed to delete record. %(error)s", error=str(ex)),
                    "error",
                )
                log.exception("Failed to delete record.")

            self.session.rollback()

            return False
        else:
            self.after_model_delete(model)

        return True

    # Default model actions
    def is_action_allowed(self, name: str) -> bool:
        # Check delete action permission
        if name == "delete" and not self.can_delete:
            return False

        return super().is_action_allowed(name)

    @action(
        "delete",
        lazy_gettext("Delete"),
        lazy_gettext("Are you sure you want to delete selected records?"),
    )
    def action_delete(self, ids: tuple) -> None:
        try:
            query = tools.get_query_for_ids(
                self.get_query(),
                self.model,  # type: ignore[arg-type]
                ids,
            )

            if self.fast_mass_delete:
                count = query.delete(synchronize_session=False)
            else:
                count = 0

                for m in query.all():
                    if self.delete_model(m):
                        count += 1

            self.session.commit()

            flash(
                ngettext(
                    "Record was successfully deleted.",
                    "%(count)s records were successfully deleted.",
                    count,
                    count=count,
                ),
                "success",
            )
        except Exception as ex:
            if not self.handle_view_exception(ex):
                raise

            flash(
                gettext("Failed to delete records. %(error)s", error=str(ex)), "error"
            )
