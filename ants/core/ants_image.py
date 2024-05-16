

__all__ = ['ANTsImage',
           'copy_image_info',
           'set_origin',
           'get_origin',
           'set_direction',
           'get_direction',
           'set_spacing',
           'get_spacing',
           'image_physical_space_consistency',
           'image_type_cast',
           'allclose',
           'is_image']

import os

import numpy as np
import pandas as pd

try:
    from functools import partialmethod
    HAS_PY3 = True
except:
    HAS_PY3 = False

import inspect

from .. import registration, segmentation, utils, plotting
from . import ants_image_io as iio2

_supported_ptypes = {'unsigned char', 'unsigned int', 'float', 'double'}
_supported_dtypes = {'uint8', 'uint32', 'float32', 'float64'}
_itk_to_npy_map = {
    'unsigned char': 'uint8',
    'unsigned int': 'uint32',
    'float': 'float32',
    'double': 'float64'}
_npy_to_itk_map = {
    'uint8': 'unsigned char',
    'uint32':'unsigned int',
    'float32': 'float',
    'float64': 'double'}


class ANTsImage(object):

    def __init__(self, pixeltype='float', dimension=3, components=1, pointer=None, is_rgb=False):
        """
        Initialize an ANTsImage

        Arguments
        ---------
        pixeltype : string
            ITK pixeltype of image

        dimension : integer
            number of image dimension. Does NOT include components dimension

        components : integer
            number of pixel components in the image

        pointer : py::capsule (optional)
            pybind11 capsule holding the pointer to the underlying ITK image object

        """
        ## Attributes which cant change without creating a new ANTsImage object
        self.pointer = pointer
        self.pixeltype = pixeltype
        self.dimension = dimension
        self.components = components
        self.has_components = self.components > 1
        self.dtype = _itk_to_npy_map[self.pixeltype]
        self.is_rgb = is_rgb

        self._pixelclass = 'vector' if self.has_components else 'scalar'
        self._shortpclass = 'V' if self._pixelclass == 'vector' else ''
        if is_rgb:
            self._pixelclass = 'rgb'
            self._shortpclass = 'RGB'

        self._libsuffix = '%s%s%i' % (self._shortpclass, utils.short_ptype(self.pixeltype), self.dimension)

        self.shape = tuple(utils.get_lib_fn('getShape')(self.pointer))
        self.physical_shape = tuple([round(sh*sp,3) for sh,sp in zip(self.shape, self.spacing)])

        self._array = None

    @property
    def spacing(self):
        """
        Get image spacing

        Returns
        -------
        tuple
        """
        libfn = utils.get_lib_fn('getSpacing')
        return tuple(libfn(self.pointer))

    def set_spacing(self, new_spacing):
        """
        Set image spacing

        Arguments
        ---------
        new_spacing : tuple or list
            updated spacing for the image.
            should have one value for each dimension

        Returns
        -------
        None
        """
        if not isinstance(new_spacing, (tuple, list)):
            raise ValueError('arg must be tuple or list')
        if len(new_spacing) != self.dimension:
            raise ValueError('must give a spacing value for each dimension (%i)' % self.dimension)

        libfn = utils.get_lib_fn('setSpacing')
        libfn(self.pointer, new_spacing)

    @property
    def origin(self):
        """
        Get image origin

        Returns
        -------
        tuple
        """
        libfn = utils.get_lib_fn('getOrigin')
        return tuple(libfn(self.pointer))

    def set_origin(self, new_origin):
        """
        Set image origin

        Arguments
        ---------
        new_origin : tuple or list
            updated origin for the image.
            should have one value for each dimension

        Returns
        -------
        None
        """
        if not isinstance(new_origin, (tuple, list)):
            raise ValueError('arg must be tuple or list')
        if len(new_origin) != self.dimension:
            raise ValueError('must give a origin value for each dimension (%i)' % self.dimension)

        libfn = utils.get_lib_fn('setOrigin')
        libfn(self.pointer, new_origin)

    @property
    def direction(self):
        """
        Get image direction

        Returns
        -------
        tuple
        """
        libfn = utils.get_lib_fn('getDirection')
        return np.array(libfn(self.pointer)).reshape(self.dimension,self.dimension)

    def set_direction(self, new_direction):
        """
        Set image direction

        Arguments
        ---------
        new_direction : numpy.ndarray or tuple or list
            updated direction for the image.
            should have one value for each dimension

        Returns
        -------
        None
        """
        if isinstance(new_direction, (tuple,list)):
            new_direction = np.asarray(new_direction)

        if not isinstance(new_direction, np.ndarray):
            raise ValueError('arg must be np.ndarray or tuple or list')
        if len(new_direction) != self.dimension:
            raise ValueError('must give a origin value for each dimension (%i)' % self.dimension)

        libfn = utils.get_lib_fn('setDirection')
        libfn(self.pointer, new_direction)

    @property
    def orientation(self):
        if self.dimension == 3:
            return self.get_orientation()
        else:
            return None

    def view(self, single_components=False):
        """
        Geet a numpy array providing direct, shared access to the image data.
        IMPORTANT: If you alter the view, then the underlying image data
        will also be altered.

        Arguments
        ---------
        single_components : boolean (default is False)
            if True, keep the extra component dimension in returned array even
            if image only has one component (i.e. self.has_components == False)

        Returns
        -------
        ndarray
        """
        if self.is_rgb:
            img = self.rgb_to_vector()
        else:
            img = self
        dtype = img.dtype
        shape = img.shape[::-1]
        if img.has_components or (single_components == True):
            shape = list(shape) + [img.components]
        libfn = utils.get_lib_fn('toNumpy')
        memview = libfn(img.pointer)
        return np.asarray(memview).view(dtype = dtype).reshape(shape).view(np.ndarray).T

    def numpy(self, single_components=False):
        """
        Get a numpy array copy representing the underlying image data. Altering
        this ndarray will have NO effect on the underlying image data.

        Arguments
        ---------
        single_components : boolean (default is False)
            if True, keep the extra component dimension in returned array even
            if image only has one component (i.e. self.has_components == False)

        Returns
        -------
        ndarray
        """
        array = np.array(self.view(single_components=single_components), copy=True, dtype=self.dtype)
        if self.has_components or (single_components == True):
            array = np.rollaxis(array, 0, self.dimension+1)
        return array

    def clone(self, pixeltype=None):
        """
        Create a copy of the given ANTsImage with the same data and info, possibly with
        a different data type for the image data. Only supports casting to
        uint8 (unsigned char), uint32 (unsigned int), float32 (float), and float64 (double)

        Arguments
        ---------
        dtype: string (optional)
            if None, the dtype will be the same as the cloned ANTsImage. Otherwise,
            the data will be cast to this type. This can be a numpy type or an ITK
            type.
            Options:
                'unsigned char' or 'uint8',
                'unsigned int' or 'uint32',
                'float' or 'float32',
                'double' or 'float64'

        Returns
        -------
        ANTsImage
        """
        if pixeltype is None:
            pixeltype = self.pixeltype

        if pixeltype not in _supported_ptypes:
            raise ValueError('Pixeltype %s not supported. Supported types are %s' % (pixeltype, _supported_ptypes))

        if self.has_components and (not self.is_rgb):
            comp_imgs = utils.split_channels(self)
            comp_imgs_cloned = [comp_img.clone(pixeltype) for comp_img in comp_imgs]
            return utils.merge_channels(comp_imgs_cloned)
        else:
            p1_short = utils.short_ptype(self.pixeltype)
            p2_short = utils.short_ptype(pixeltype)
            ndim = self.dimension
            fn_suffix = '%s%i' % (p2_short,ndim)
            libfn = utils.get_lib_fn('antsImageClone%s'%fn_suffix)
            pointer_cloned = libfn(self.pointer)
            return ANTsImage(pixeltype=pixeltype,
                            dimension=self.dimension,
                            components=self.components,
                            is_rgb=self.is_rgb,
                            pointer=pointer_cloned)

    # pythonic alias for `clone` is `copy`
    copy = clone

    def astype(self, dtype):
        """
        Cast & clone an ANTsImage to a given numpy datatype.

        Map:
            uint8   : unsigned char
            uint32  : unsigned int
            float32 : float
            float64 : double
        """
        if dtype not in _supported_dtypes:
            raise ValueError('Datatype %s not supported. Supported types are %s' % (dtype, _supported_dtypes))

        pixeltype = _npy_to_itk_map[dtype]
        return self.clone(pixeltype)

    def new_image_like(self, data):
        """
        Create a new ANTsImage with the same header information, but with
        a new image array.

        Arguments
        ---------
        data : ndarray or py::capsule
            New array or pointer for the image.
            It must have the same shape as the current
            image data.

        Returns
        -------
        ANTsImage
        """
        if not isinstance(data, np.ndarray):
            raise ValueError('data must be a numpy array')
        if not self.has_components:
            if data.shape != self.shape:
                raise ValueError('given array shape (%s) and image array shape (%s) do not match' % (data.shape, self.shape))
        else:
            if (data.shape[-1] != self.components) or (data.shape[:-1] != self.shape):
                raise ValueError('given array shape (%s) and image array shape (%s) do not match' % (data.shape[1:], self.shape))

        return iio2.from_numpy(data, origin=self.origin,
            spacing=self.spacing, direction=self.direction,
            has_components=self.has_components)

    def to_file(self, filename):
        """
        Write the ANTsImage to file

        Args
        ----
        filename : string
            filepath to which the image will be written
        """
        filename = os.path.expanduser(filename)
        libfn = utils.get_lib_fn('toFile')
        libfn(self.pointer, filename)
    to_filename = to_file

    def apply(self, fn):
        """
        Apply an arbitrary function to ANTsImage.

        Args
        ----
        fn : python function or lambda
            function to apply to ENTIRE image at once

        Returns
        -------
        ANTsImage
            image with function applied to it
        """
        this_array = self.numpy()
        new_array = fn(this_array)
        return self.new_image_like(new_array)

    ## NUMPY FUNCTIONS ##
    def abs(self, axis=None):
        """ Return absolute value of image """
        return np.abs(self.numpy())
    def mean(self, axis=None):
        """ Return mean along specified axis """
        return self.numpy().mean(axis=axis)
    def median(self, axis=None):
        """ Return median along specified axis """
        return np.median(self.numpy(), axis=axis)
    def std(self, axis=None):
        """ Return std along specified axis """
        return self.numpy().std(axis=axis)
    def sum(self, axis=None, keepdims=False):
        """ Return sum along specified axis """
        return self.numpy().sum(axis=axis, keepdims=keepdims)
    def min(self, axis=None):
        """ Return min along specified axis """
        return self.numpy().min(axis=axis)
    def max(self, axis=None):
        """ Return max along specified axis """
        return self.numpy().max(axis=axis)
    def range(self, axis=None):
        """ Return range tuple along specified axis """
        return (self.min(axis=axis), self.max(axis=axis))
    def argmin(self, axis=None):
        """ Return argmin along specified axis """
        return self.numpy().argmin(axis=axis)
    def argmax(self, axis=None):
        """ Return argmax along specified axis """
        return self.numpy().argmax(axis=axis)
    def argrange(self, axis=None):
        """ Return argrange along specified axis """
        amin = self.argmin(axis=axis)
        amax = self.argmax(axis=axis)
        if axis is None:
            return (amin, amax)
        else:
            return np.stack([amin, amax]).T
    def flatten(self):
        """ Flatten image data """
        return self.numpy().flatten()
    def nonzero(self):
        """ Return non-zero indices of image """
        return self.numpy().nonzero()
    def unique(self, sort=False):
        """ Return unique set of values in image """
        unique_vals = np.unique(self.numpy())
        if sort:
            unique_vals = np.sort(unique_vals)
        return unique_vals

    ## OVERLOADED OPERATORS ##
    def __add__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array + other
        return self.new_image_like(new_array)
    
    __radd__ = __add__
    
    def __sub__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array - other
        return self.new_image_like(new_array)
    
    def __rsub__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = other - this_array
        return self.new_image_like(new_array)
    
    def __mul__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array * other
        return self.new_image_like(new_array)

    __rmul__ = __mul__

    def __truediv__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array / other
        return self.new_image_like(new_array)

    def __pow__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array ** other
        return self.new_image_like(new_array)

    def __gt__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array > other
        return self.new_image_like(new_array.astype('uint8'))

    def __ge__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array >= other
        return self.new_image_like(new_array.astype('uint8'))

    def __lt__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array < other
        return self.new_image_like(new_array.astype('uint8'))

    def __le__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array <= other
        return self.new_image_like(new_array.astype('uint8'))

    def __eq__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array == other
        return self.new_image_like(new_array.astype('uint8'))

    def __ne__(self, other):
        this_array = self.numpy()

        if isinstance(other, ANTsImage):
            if not image_physical_space_consistency(self, other):
                raise ValueError('images do not occupy same physical space')
            other = other.numpy()

        new_array = this_array != other
        return self.new_image_like(new_array.astype('uint8'))

    def __getitem__(self, idx):
        if self._array is None:
            self._array = self.numpy()

        if isinstance(idx, ANTsImage):
            if not image_physical_space_consistency(self, idx):
                raise ValueError('images do not occupy same physical space')
            return self._array.__getitem__(idx.numpy().astype('bool'))
        else:
            return self._array.__getitem__(idx)


    def __setitem__(self, idx, value):
        arr = self.view()
        if isinstance(idx, ANTsImage):
            if not image_physical_space_consistency(self, idx):
                raise ValueError('images do not occupy same physical space')
            arr.__setitem__(idx.numpy().astype('bool'), value)
        else:
            arr.__setitem__(idx, value)

    def __repr__(self):
        if self.dimension == 3:
            s = 'ANTsImage ({})\n'.format(self.orientation)
        else:
            s = 'ANTsImage\n'
        s = s +\
            '\t {:<10} : {} ({})\n'.format('Pixel Type', self.pixeltype, self.dtype)+\
            '\t {:<10} : {}{}\n'.format('Components', self.components, ' (RGB)' if 'RGB' in self._libsuffix else '')+\
            '\t {:<10} : {}\n'.format('Dimensions', self.shape)+\
            '\t {:<10} : {}\n'.format('Spacing', tuple([round(s,4) for s in self.spacing]))+\
            '\t {:<10} : {}\n'.format('Origin', tuple([round(o,4) for o in self.origin]))+\
            '\t {:<10} : {}\n'.format('Direction', np.round(self.direction.flatten(),4))
        return s

if HAS_PY3:
    # Set partial class methods for any functions which take an ANTsImage as the first argument
    for k, v in utils.__dict__.items():
        if callable(v):
            args = inspect.getfullargspec(getattr(utils,k)).args
            if (len(args) > 0) and (args[0] in {'img','image','cropped_image'}):
                setattr(ANTsImage, k, partialmethod(v))

    for k, v in registration.__dict__.items():
        if callable(v):
            args = inspect.getfullargspec(getattr(registration,k)).args
            if (len(args) > 0) and (args[0] in {'img','image'}):
                setattr(ANTsImage, k, partialmethod(v))

    for k, v in segmentation.__dict__.items():
        if callable(v):
            args = inspect.getfullargspec(getattr(segmentation,k)).args
            if (len(args) > 0) and (args[0] in {'img','image'}):
                setattr(ANTsImage, k, partialmethod(v))

    for k, v in plotting.__dict__.items():
        if callable(v):
            args = inspect.getfullargspec(getattr(plotting,k)).args
            if (len(args) > 0) and (args[0] in {'img','image'}):
                setattr(ANTsImage, k, partialmethod(v))


class Dictlist(dict):
    def __setitem__(self, key, value):
        try:
            self[key]
        except KeyError:
            super(Dictlist, self).__setitem__(key, [])
        self[key].append(value)


def copy_image_info(reference, target):
    """
    Copy origin, direction, and spacing from one antsImage to another

    ANTsR function: `antsCopyImageInfo`

    Arguments
    ---------
    reference : ANTsImage
        Image to get values from.
    target  : ANTsImAGE
        Image to copy values to

    Returns
    -------
    ANTsImage
        Target image with reference header information
    """
    target.set_origin(reference.origin)
    target.set_direction(reference.direction)
    target.set_spacing(reference.spacing)
    return target

def set_origin(image, origin):
    """
    Set origin of ANTsImage

    ANTsR function: `antsSetOrigin`
    """
    image.set_origin(origin)

def get_origin(image):
    """
    Get origin of ANTsImage

    ANTsR function: `antsGetOrigin`
    """

    return image.origin

def set_direction(image, direction):
    """
    Set direction of ANTsImage

    ANTsR function: `antsSetDirection`
    """
    image.set_direction(direction)

def get_direction(image):
    """
    Get direction of ANTsImage

    ANTsR function: `antsGetDirection`
    """
    return image.direction

def set_spacing(image, spacing):
    """
    Set spacing of ANTsImage

    ANTsR function: `antsSetSpacing`
    """
    image.set_spacing(spacing)

def get_spacing(image):
    """
    Get spacing of ANTsImage

    ANTsR function: `antsGetSpacing`
    """
    return image.spacing


def image_physical_space_consistency(image1, image2, tolerance=1e-2, datatype=False):
    """
    Check if two or more ANTsImage objects occupy the same physical space

    ANTsR function: `antsImagePhysicalSpaceConsistency`

    Arguments
    ---------
    *images : ANTsImages
        images to compare

    tolerance : float
        tolerance when checking origin and spacing

    data_type : boolean
        If true, also check that the image data types are the same

    Returns
    -------
    boolean
        true if images share same physical space, false otherwise
    """
    images = [image1, image2]

    img1 = images[0]
    for img2 in images[1:]:
        if (not isinstance(img1, ANTsImage)) or (not isinstance(img2, ANTsImage)):
            raise ValueError('Both images must be of class `AntsImage`')

        # image dimension check
        if img1.dimension != img2.dimension:
            return False

        # image spacing check
        space_diffs = sum([abs(s1-s2)>tolerance for s1, s2 in zip(img1.spacing, img2.spacing)])
        if space_diffs > 0:
            return False

        # image origin check
        origin_diffs = sum([abs(s1-s2)>tolerance for s1, s2 in zip(img1.origin, img2.origin)])
        if origin_diffs > 0:
            return False

        # image direction check
        origin_diff = np.allclose(img1.direction, img2.direction, atol=tolerance)
        if not origin_diff:
            return False

        # data type
        if datatype == True:
            if img1.pixeltype != img2.pixeltype:
                return False

            if img1.components != img2.components:
                return False

    return True


def image_type_cast(image_list, pixeltype=None):
    """
    Cast a list of images to the highest pixeltype present in the list
    or all to a specified type

    ANTsR function: `antsImageTypeCast`

    Arguments
    ---------
    image_list : list/tuple
        images to cast

    pixeltype : string (optional)
        pixeltype to cast to. If None, images will be cast to the highest
        precision pixeltype found in image_list

    Returns
    -------
    list of ANTsImages
        given images casted to new type
    """
    if not isinstance(image_list, (list,tuple)):
        raise ValueError('image_list must be list of ANTsImage types')

    pixtypes = []
    for img in image_list:
        pixtypes.append(img.pixeltype)

    if pixeltype is None:
        pixeltype = 'unsigned char'
        for p in pixtypes:
            if p == 'double':
                pixeltype = 'double'
            elif (p=='float') and (pixeltype!='double'):
                pixeltype = 'float'
            elif (p=='unsigned int') and (pixeltype!='float') and (pixeltype!='double'):
                pixeltype = 'unsigned int'

    out_images = []
    for img in image_list:
        if img.pixeltype == pixeltype:
            out_images.append(img)
        else:
            out_images.append(img.clone(pixeltype))

    return out_images


def allclose(image1, image2):
    """
    Check if two images have the same array values
    """
    return np.allclose(image1.numpy(), image2.numpy())

def is_image(image):
    return isinstance(image, ANTsImage)