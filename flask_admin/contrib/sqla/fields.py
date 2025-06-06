"""
Useful form fields for use with SQLAlchemy ORM.
"""

import operator
import typing as t

from sqlalchemy.orm.util import identity_key
from wtforms import form
from wtforms.fields import SelectFieldBase
from wtforms.fields import StringField
from wtforms.utils import unset_value
from wtforms.utils import UnsetValue
from wtforms.validators import ValidationError

from flask_admin._compat import _iter_choices_wtforms_compat
from flask_admin._compat import iteritems
from flask_admin._compat import string_types
from flask_admin._compat import text_type
from flask_admin.babel import lazy_gettext
from flask_admin.contrib.sqla.widgets import CheckboxListInput
from flask_admin.form import BaseForm
from flask_admin.form import FormOpts
from flask_admin.form import Select2Widget
from flask_admin.model.fields import InlineFieldList
from flask_admin.model.fields import InlineModelFormField

from ..._types import T_ITER_CHOICES
from ..._types import T_ORM_MODEL
from ..._types import T_SQLALCHEMY_MODEL
from ..._types import T_SQLALCHEMY_SESSION
from ..._types import T_VALIDATOR
from ...model.form import InlineBaseFormAdmin
from .tools import get_primary_key


class QuerySelectField(SelectFieldBase):
    """
    Will display a select drop-down field to choose between ORM results in a
    sqlalchemy `Query`.  The `data` property actually will store/keep an ORM
    model instance, not the ID. Submitting a choice which is not in the query
    will result in a validation error.

    This field only works for queries on models whose primary key column(s)
    have a consistent string representation. This means it mostly only works
    for those composed of string, unicode, and integer types. For the most
    part, the primary keys will be auto-detected from the model, alternately
    pass a one-argument callable to `get_pk` which can return a unique
    comparable key.

    The `query` property on the field can be set from within a view to assign
    a query per-instance to the field. If the property is not set, the
    `query_factory` callable passed to the field constructor will be called to
    obtain a query.

    Specify `get_label` to customize the label associated with each option. If
    a string, this is the name of an attribute on the model object to use as
    the label text. If a one-argument callable, this callable will be passed
    model instance and expected to return the label text. Otherwise, the model
    object's `__str__` or `__unicode__` will be used.

    If `allow_blank` is set to `True`, then a blank choice will be added to the
    top of the list. Selecting this choice will result in the `data` property
    being `None`. The label for this blank choice can be set by specifying the
    `blank_text` parameter.
    """

    widget = Select2Widget()

    def __init__(
        self,
        label: t.Optional[str] = None,
        validators: t.Union[list[T_VALIDATOR], tuple[T_VALIDATOR, ...], None] = None,
        query_factory: t.Any = None,
        get_pk: t.Any = None,
        get_label: t.Any = None,
        allow_blank: bool = False,
        blank_text: str = "",
        **kwargs: t.Any,
    ):
        super().__init__(
            label,
            validators,  # type: ignore[arg-type]
            **kwargs,
        )
        self.query_factory = query_factory

        if get_pk is None:
            self.get_pk = get_pk_from_identity
        else:
            self.get_pk = get_pk

        if get_label is None:
            self.get_label = lambda x: x
        elif isinstance(get_label, string_types):
            self.get_label = operator.attrgetter(get_label)
        else:
            self.get_label = get_label

        self.allow_blank = allow_blank
        self.blank_text = blank_text
        self.query = None
        self._object_list: t.Optional[list[tuple[str, t.Any]]] = None

    def _get_data(self) -> t.Any:
        if self._formdata is not None:
            for pk, obj in self._get_object_list():
                if pk == self._formdata:
                    self._set_data(obj)
                    break
        return self._data

    def _set_data(self, data: t.Any) -> None:
        self._data = data
        self._formdata: t.Union[set, str, None] = None

    data = property(_get_data, _set_data)

    def _get_object_list(self) -> list[tuple[str, t.Any]]:
        if self._object_list is None:
            query = self.query or self.query_factory()
            get_pk = self.get_pk
            self._object_list = [(text_type(get_pk(obj)), obj) for obj in query]
        return self._object_list

    def iter_choices(self) -> t.Iterator[T_ITER_CHOICES]:  # type: ignore[override]
        if self.allow_blank:
            yield _iter_choices_wtforms_compat(
                "__None", self.blank_text, self.data is None
            )

        for pk, obj in self._get_object_list():
            yield _iter_choices_wtforms_compat(
                pk, self.get_label(obj), obj == self.data
            )

    def process_formdata(self, valuelist: list[str]) -> None:
        if valuelist:
            if self.allow_blank and valuelist[0] == "__None":
                self.data = None
            else:
                self._data = None
                self._formdata = valuelist[0]

    def pre_validate(self, form: form.BaseForm) -> None:
        if not self.allow_blank or self.data is not None:
            for _pk, obj in self._get_object_list():
                if self.data == obj:
                    break
            else:
                raise ValidationError(self.gettext("Not a valid choice"))


class QuerySelectMultipleField(QuerySelectField):
    """
    Very similar to QuerySelectField with the difference that this will
    display a multiple select. The data property will hold a list with ORM
    model instances and will be an empty list when no value is selected.

    If any of the items in the data list or submitted form data cannot be
    found in the query, this will result in a validation error.
    """

    widget = Select2Widget(multiple=True)

    def __init__(
        self,
        label: t.Optional[str] = None,
        validators: t.Optional[list[T_VALIDATOR]] = None,
        default: t.Any = None,
        **kwargs: t.Any,
    ) -> None:
        if default is None:
            default = []
        super().__init__(label, validators, default=default, **kwargs)
        self._invalid_formdata = False

    def _get_data(self) -> t.Any:
        formdata = self._formdata
        if formdata is not None:
            data = []
            for pk, obj in self._get_object_list():
                if not formdata:
                    break
                elif pk in formdata:
                    formdata.remove(pk)
                    data.append(obj)
            if formdata:
                self._invalid_formdata = True
            self._set_data(data)
        return self._data

    def _set_data(self, data: list[t.Any]) -> None:
        self._data = data
        self._formdata: t.Optional[set] = None

    data = property(_get_data, _set_data)

    def iter_choices(self) -> t.Iterator[T_ITER_CHOICES]:  # type: ignore[override]
        for pk, obj in self._get_object_list():
            yield _iter_choices_wtforms_compat(
                pk, self.get_label(obj), obj in self.data
            )

    def process_formdata(self, valuelist: t.Iterable) -> None:
        self._formdata = set(valuelist)

    def pre_validate(self, form: form.BaseForm) -> None:
        if self._invalid_formdata:
            raise ValidationError(self.gettext("Not a valid choice"))
        elif self.data:
            obj_list = list(x[1] for x in self._get_object_list())
            for v in self.data:
                if v not in obj_list:
                    raise ValidationError(self.gettext("Not a valid choice"))


class CheckboxListField(QuerySelectMultipleField):
    """
    Alternative field for many-to-many relationships.

    Can be used instead of `QuerySelectMultipleField`.
    Appears as the list of checkboxes.
    Example::

        class MyView(ModelView):
            form_columns = (
                'languages',
            )
            form_args = {
                'languages': {
                    'query_factory': Language.query,
                },
            }
            form_overrides = {
                'languages': CheckboxListField,
            }
    """

    widget = CheckboxListInput()  # type: ignore[assignment]


class HstoreForm(BaseForm):
    """Form used in InlineFormField/InlineHstoreList for HSTORE columns"""

    key = StringField(lazy_gettext("Key"))
    value = StringField(lazy_gettext("Value"))


class KeyValue:
    """Used by InlineHstoreList to simulate a key and a value field instead of
    the single HSTORE column."""

    def __init__(
        self, key: t.Optional[str] = None, value: t.Optional[str] = None
    ) -> None:
        self.key = key
        self.value = value


class InlineHstoreList(InlineFieldList):
    """Version of InlineFieldList for use with Postgres HSTORE columns"""

    def process(
        self,
        formdata: t.Optional[dict],  # type: ignore[override]
        data: t.Union[UnsetValue, list[KeyValue]] = unset_value,
        extra_filters: t.Any = None,
    ) -> None:
        """SQLAlchemy returns a dict for HSTORE columns, but WTForms cannot
        process a dict. This overrides `process` to convert the dict
        returned by SQLAlchemy to a list of classes before processing."""
        if isinstance(data, dict):
            data = [KeyValue(k, v) for k, v in iteritems(data)]
        super().process(formdata, data, extra_filters)

    def populate_obj(self, obj: t.Any, name: str) -> None:
        """Combines each FormField key/value into a dictionary for storage"""
        _fake = type("_fake", (object,), {})

        output = {}
        for form_field in self.entries:
            if not self.should_delete(form_field):
                fake_obj = _fake()
                fake_obj.data = KeyValue()
                form_field.populate_obj(fake_obj, "data")
                output[fake_obj.data.key] = fake_obj.data.value

        setattr(obj, name, output)


class InlineModelFormList(InlineFieldList):
    """
    Customized inline model form list field.
    """

    form_field_type = InlineModelFormField
    """
        Form field type. Override to use custom field for each inline form
    """

    def __init__(
        self,
        form: type[form.BaseForm],
        session: T_SQLALCHEMY_SESSION,
        model: type[T_SQLALCHEMY_MODEL],
        prop: str,
        inline_view: t.Any,
        **kwargs: t.Any,
    ) -> None:
        """
        Default constructor.

        :param form:
            Form for the related model
        :param session:
            SQLAlchemy session
        :param model:
            Related model
        :param prop:
            Related property name
        :param inline_view:
            Inline view
        """
        self.form = form
        self.session = session
        self.model = model
        self.prop = prop
        self.inline_view = inline_view

        self._pk = get_primary_key(model)

        # Generate inline form field
        form_opts = FormOpts(
            widget_args=getattr(inline_view, "form_widget_args", None),
            form_rules=inline_view._form_rules,
        )

        form_field = self.form_field_type(
            form,
            self._pk,  # type: ignore[arg-type]
            form_opts=form_opts,
        )

        super().__init__(form_field, **kwargs)

    def display_row_controls(self, field: InlineModelFormField) -> bool:
        return field.get_pk() is not None

    def populate_obj(self, obj: t.Any, name: str) -> None:
        values = getattr(obj, name, None)

        if values is None:
            return

        # Create primary key map
        pk_map = dict((get_obj_pk(v, self._pk), v) for v in values)

        # Handle request data
        for field in self.entries:
            field_id = get_field_id(field)

            is_created = field_id not in pk_map
            if not is_created:
                model = pk_map[field_id]

                if self.should_delete(field):
                    self.session.delete(model)
                    continue
            else:
                model = self.model()
                values.append(model)

            field.populate_obj(model, None)

            self.inline_view._on_model_change(field, model, is_created)


class InlineModelOneToOneField(InlineModelFormField):
    def __init__(
        self,
        form: type[form.BaseForm],
        session: T_SQLALCHEMY_SESSION,
        model: type[T_ORM_MODEL],
        prop: str,
        inline_view: InlineBaseFormAdmin,
        **kwargs: t.Any,
    ) -> None:
        self.form = form
        self.session = session
        self.model = model
        self.prop = prop
        self.inline_view = inline_view

        self._pk: t.Union[tuple[t.Any, ...], t.Any] = get_primary_key(model)  # type: ignore[assignment]

        # Generate inline form field
        form_opts = FormOpts(
            widget_args=getattr(inline_view, "form_widget_args", None),
            form_rules=inline_view._form_rules,
        )
        super().__init__(form, self._pk, form_opts=form_opts, **kwargs)  # type: ignore[arg-type]

    @staticmethod
    def _looks_empty(field: t.Optional[t.Any]) -> bool:
        """
        Check while installed fields is not null
        """
        if field is None:
            return True

        if isinstance(field, str) and not field:
            return True

        return False

    def populate_obj(self, model: t.Any, field_name: str) -> None:
        inline_model = getattr(model, field_name, None)
        is_created = False
        form_is_empty = True

        if not inline_model:
            is_created = True
            inline_model = self.model()

        # iterate all inline form fields and fill model
        for name, field in iteritems(self.form._fields):
            if name != self._pk:
                field.populate_obj(inline_model, name)

            if form_is_empty and not self._looks_empty(field.data):
                form_is_empty = False

        # don't create inline model if perhaps one field was not filled
        if form_is_empty:
            return

        # set for our model updated inline model
        setattr(model, field_name, inline_model)

        # save results
        self.inline_view.on_model_change(self.form, model, is_created)


def get_pk_from_identity(obj: t.Any) -> str:
    # TODO: Remove me
    key = identity_key(instance=obj)[1]
    return ":".join(text_type(x) for x in key)


def get_obj_pk(
    obj: t.Any, pk: t.Union[str, tuple[str, ...]]
) -> t.Union[str, tuple[str, ...]]:
    """
    get and format pk from obj
    :rtype: text_type
    """

    if isinstance(pk, tuple):
        return tuple(text_type(getattr(obj, k)) for k in pk)

    return text_type(getattr(obj, pk))


def get_field_id(field: InlineModelFormField) -> t.Union[tuple[str, ...], str]:
    """
    get and format id from field
    :rtype: text_type
    """
    field_id = field.get_pk()
    if isinstance(field_id, tuple):
        return tuple(text_type(_) for _ in field_id)

    return text_type(field_id)
